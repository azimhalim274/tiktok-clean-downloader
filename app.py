import asyncio
import io
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
TEMP_DIR = BASE_DIR / "temp"
STATIC_DIR = BASE_DIR / "static"
HISTORY_FILE = DOWNLOADS_DIR / "history.json"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="TikTok Clean Downloader")

TIKTOK_URL_PATTERN = re.compile(
    r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/[^\s,\"'<>]+"
)

jobs_store: Dict[str, dict] = {}
history_lock = threading.Lock()
download_semaphore = asyncio.Semaphore(3)


class BatchRequest(BaseModel):
    urls: List[str]
    remove_audio: bool = True


def sanitize_filename(name: str, max_len: int = 50) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")
    return cleaned[:max_len] if cleaned else "video"


def extract_tiktok_urls(raw_inputs: List[str]) -> List[str]:
    found: List[str] = []
    seen = set()
    for chunk in raw_inputs:
        for match in TIKTOK_URL_PATTERN.findall(chunk or ""):
            url = match.rstrip(").,]")
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def cleanup_old_files(max_age_seconds: int = 7200, max_files: int = 30) -> None:
    """Buang fail MP4 lama supaya storan cloud (Render) tidak penuh."""
    mp4_files = sorted(
        DOWNLOADS_DIR.glob("*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    now = time.time()
    for idx, file_path in enumerate(mp4_files):
        try:
            age = now - file_path.stat().st_mtime
            if idx >= max_files or age > max_age_seconds:
                file_path.unlink(missing_ok=True)
        except Exception:
            pass


def load_history() -> List[dict]:
    with history_lock:
        if not HISTORY_FILE.exists():
            return []
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
        valid = [item for item in data if (DOWNLOADS_DIR / item.get("filename", "")).exists()]
        if len(valid) != len(data):
            HISTORY_FILE.write_text(json.dumps(valid, indent=2, ensure_ascii=False), encoding="utf-8")
        return valid


def append_history(entry: dict) -> None:
    with history_lock:
        items: List[dict] = []
        if HISTORY_FILE.exists():
            try:
                items = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                items = []
        items.insert(0, entry)
        items = items[:100]
        HISTORY_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def remove_from_history(filename: str) -> None:
    with history_lock:
        if not HISTORY_FILE.exists():
            return
        try:
            items = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        items = [i for i in items if i.get("filename") != filename]
        HISTORY_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def download_via_ytdlp(url: str, work_dir: Path) -> dict:
    output_template = str(work_dir / "source.%(ext)s")
    ydl_opts = {
        "outtmpl": output_template,
        "format": "best[format_id!*=watermarked][ext=mp4]/best[format_id!*=watermarked]/best[ext=mp4]/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise RuntimeError("Tiada maklumat video ditemui.")

        video_files = sorted(
            [p for p in work_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}],
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if not video_files:
            raise RuntimeError("Fail video gagal dimuat turun oleh yt-dlp.")

        return {
            "source_path": video_files[0],
            "video_id": str(info.get("id") or uuid.uuid4().hex[:8]),
            "title": (info.get("title") or info.get("description") or "Video TikTok").strip(),
            "author": (info.get("uploader") or info.get("creator") or info.get("channel") or "tiktok").strip(),
            "thumbnail": info.get("thumbnail") or "",
            "duration": info.get("duration") or 0,
            "method": "yt-dlp",
        }


def download_via_tikwm(url: str, work_dir: Path) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    }
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        resp = client.post("https://www.tikwm.com/api/", data={"url": url, "hd": 1})
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0 or not payload.get("data"):
            raise RuntimeError(payload.get("msg") or "TikWM API gagal mendapatkan video.")

        data = payload["data"]
        video_url = data.get("hdplay") or data.get("play")
        if not video_url:
            raise RuntimeError("Pautan video tanpa watermark tidak ditemui.")

        source_path = work_dir / "source.mp4"
        with client.stream("GET", video_url) as stream_resp:
            stream_resp.raise_for_status()
            with source_path.open("wb") as f:
                for chunk in stream_resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)

        author_info = data.get("author") or {}
        return {
            "source_path": source_path,
            "video_id": str(data.get("id") or uuid.uuid4().hex[:8]),
            "title": (data.get("title") or "Video TikTok").strip(),
            "author": (author_info.get("unique_id") or author_info.get("nickname") or "tiktok").strip(),
            "thumbnail": data.get("cover") or data.get("origin_cover") or "",
            "duration": data.get("duration") or 0,
            "method": "tikwm",
        }


def process_audio_ffmpeg(source_path: Path, output_path: Path, remove_audio: bool) -> None:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("FFmpeg tidak ditemui dalam sistem PATH.")

    if remove_audio:
        fast_cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(source_path),
            "-c:v",
            "copy",
            "-an",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(fast_cmd, capture_output=True, text=True)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            fallback_cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                str(source_path),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "22",
                "-an",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
            fallback = subprocess.run(fallback_cmd, capture_output=True, text=True)
            if fallback.returncode != 0:
                raise RuntimeError(f"FFmpeg gagal membuang audio: {fallback.stderr[-300:]}")
    else:
        copy_cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(source_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(copy_cmd, capture_output=True, text=True)
        if result.returncode != 0 or not output_path.exists():
            shutil.copy2(source_path, output_path)


def run_single_download_sync(item: dict, remove_audio: bool) -> None:
    work_dir = TEMP_DIR / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        item["status"] = "downloading"
        item["message"] = "Memuat turun video tanpa watermark..."

        meta = None
        ytdlp_err = None
        try:
            meta = download_via_ytdlp(item["url"], work_dir)
        except Exception as exc:
            ytdlp_err = str(exc)
            try:
                meta = download_via_tikwm(item["url"], work_dir)
            except Exception as exc2:
                raise RuntimeError(f"Gagal memuat turun video. ({ytdlp_err} | Fallback: {exc2})")

        item["title"] = meta["title"][:120]
        item["author"] = meta["author"]
        item["thumbnail"] = meta["thumbnail"]
        item["duration"] = meta["duration"]

        if remove_audio:
            item["status"] = "removing_audio"
            item["message"] = "Membuang sound (audio)..."
        else:
            item["status"] = "processing"
            item["message"] = "Menyediakan fail MP4..."

        author_slug = sanitize_filename(meta["author"], 25)
        video_id_slug = sanitize_filename(meta["video_id"], 25)
        suffix = "muted" if remove_audio else "clean"
        unique_tag = uuid.uuid4().hex[:6]
        final_filename = f"tiktok_{author_slug}_{video_id_slug}_{suffix}_{unique_tag}.mp4"
        final_path = DOWNLOADS_DIR / final_filename

        process_audio_ffmpeg(meta["source_path"], final_path, remove_audio=remove_audio)

        file_size = final_path.stat().st_size
        item["status"] = "done"
        item["message"] = "Selesai"
        item["filename"] = final_filename
        item["file_size"] = file_size
        item["remove_audio"] = remove_audio
        item["download_url"] = f"/api/files/{final_filename}?download=1"
        item["preview_url"] = f"/api/files/{final_filename}"
        item["created_at"] = datetime.now().isoformat(timespec="seconds")

        append_history(
            {
                "filename": final_filename,
                "url": item["url"],
                "title": item["title"],
                "author": item["author"],
                "thumbnail": item["thumbnail"],
                "file_size": file_size,
                "remove_audio": remove_audio,
                "download_url": item["download_url"],
                "preview_url": item["preview_url"],
                "created_at": item["created_at"],
            }
        )
    except Exception as exc:
        item["status"] = "error"
        item["message"] = str(exc)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def process_batch_job(job_id: str) -> None:
    job = jobs_store[job_id]
    remove_audio = job["remove_audio"]

    async def worker(item: dict) -> None:
        async with download_semaphore:
            await asyncio.to_thread(run_single_download_sync, item, remove_audio)

    await asyncio.gather(*(worker(item) for item in job["items"]))
    job["completed"] = True


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "yt_dlp_version": yt_dlp.version.__version__,
        "downloads_dir": str(DOWNLOADS_DIR),
    }


@app.post("/api/batch")
async def create_batch_job(req: BatchRequest):
    cleanup_old_files()
    urls = extract_tiktok_urls(req.urls)
    if not urls:
        raise HTTPException(
            status_code=400,
            detail="Tiada pautan TikTok yang sah ditemui. Pastikan link bermula dengan https://www.tiktok.com atau https://vt.tiktok.com.",
        )

    job_id = uuid.uuid4().hex[:12]
    items = [
        {
            "id": uuid.uuid4().hex[:8],
            "url": url,
            "status": "queued",
            "message": "Dalam giliran...",
            "title": "",
            "author": "",
            "thumbnail": "",
            "duration": 0,
            "filename": "",
            "file_size": 0,
            "remove_audio": req.remove_audio,
            "download_url": "",
            "preview_url": "",
            "created_at": "",
        }
        for url in urls
    ]

    jobs_store[job_id] = {
        "job_id": job_id,
        "remove_audio": req.remove_audio,
        "completed": False,
        "total": len(items),
        "items": items,
    }

    asyncio.create_task(process_batch_job(job_id))
    return jobs_store[job_id]


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    job = jobs_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemui.")
    done_count = sum(1 for i in job["items"] if i["status"] == "done")
    error_count = sum(1 for i in job["items"] if i["status"] == "error")
    return {
        **job,
        "done_count": done_count,
        "error_count": error_count,
    }


@app.get("/api/jobs/{job_id}/zip")
def download_job_zip(job_id: str):
    job = jobs_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemui.")

    completed_files = [
        DOWNLOADS_DIR / i["filename"]
        for i in job["items"]
        if i["status"] == "done" and i.get("filename") and (DOWNLOADS_DIR / i["filename"]).exists()
    ]
    if not completed_files:
        raise HTTPException(status_code=400, detail="Tiada video yang siap untuk dimuat turun.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for file_path in completed_files:
            zf.write(file_path, arcname=file_path.name)
    buffer.seek(0)

    zip_name = f"tiktok_batch_{job_id}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@app.get("/api/files/{filename}")
def get_file(filename: str, download: int = Query(default=0)):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nama fail tidak sah.")
    file_path = DOWNLOADS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Fail tidak ditemui.")

    if download:
        return FileResponse(
            path=file_path,
            media_type="video/mp4",
            filename=filename,
        )
    return FileResponse(path=file_path, media_type="video/mp4")


@app.get("/api/history")
def get_history():
    return {"items": load_history(), "downloads_dir": str(DOWNLOADS_DIR)}


@app.delete("/api/history/{filename}")
def delete_history_item(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nama fail tidak sah.")
    file_path = DOWNLOADS_DIR / filename
    if file_path.exists():
        file_path.unlink()
    remove_from_history(filename)
    return {"deleted": filename}


@app.delete("/api/history")
def clear_all_history():
    for file_path in DOWNLOADS_DIR.glob("*.mp4"):
        try:
            file_path.unlink()
        except Exception:
            pass
    with history_lock:
        if HISTORY_FILE.exists():
            HISTORY_FILE.write_text("[]", encoding="utf-8")
    return {"cleared": True}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")
