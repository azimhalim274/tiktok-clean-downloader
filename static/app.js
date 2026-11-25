const TIKTOK_REGEX = /https?:\/\/(?:www\.|m\.|vm\.|vt\.)?tiktok\.com\/[^\s,"'<>]+/gi;

const urlInput = document.getElementById("urlInput");
const linkCounter = document.getElementById("linkCounter");
const removeAudioToggle = document.getElementById("removeAudioToggle");
const audioModeHint = document.getElementById("audioModeHint");
const submitBtn = document.getElementById("submitBtn");
const pasteBtn = document.getElementById("pasteBtn");
const clearBtn = document.getElementById("clearBtn");
const formError = document.getElementById("formError");

const healthPill = document.getElementById("healthPill");
const healthText = document.getElementById("healthText");

const batchSection = document.getElementById("batchSection");
const batchSummary = document.getElementById("batchSummary");
const batchProgressBar = document.getElementById("batchProgressBar");
const batchList = document.getElementById("batchList");
const downloadZipBtn = document.getElementById("downloadZipBtn");

const historyList = document.getElementById("historyList");
const downloadsDirHint = document.getElementById("downloadsDirHint");
const clearHistoryBtn = document.getElementById("clearHistoryBtn");

const previewModal = document.getElementById("previewModal");
const modalBackdrop = document.getElementById("modalBackdrop");
const closeModalBtn = document.getElementById("closeModalBtn");
const previewTitle = document.getElementById("previewTitle");
const previewVideo = document.getElementById("previewVideo");
const previewMeta = document.getElementById("previewMeta");
const previewDownloadBtn = document.getElementById("previewDownloadBtn");

let activePollTimer = null;

function extractLinks(text) {
  const matches = text.match(TIKTOK_REGEX) || [];
  const unique = [];
  const seen = new Set();
  for (const raw of matches) {
    const cleaned = raw.replace(/[).,\]]+$/, "");
    if (!seen.has(cleaned)) {
      seen.add(cleaned);
      unique.push(cleaned);
    }
  }
  return unique;
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let val = bytes;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i++;
  }
  return `${val.toFixed(val >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function updateLinkCount() {
  const links = extractLinks(urlInput.value);
  linkCounter.textContent =
    links.length === 0
      ? "0 pautan dikesan"
      : `${links.length} pautan TikTok dikesan`;
  linkCounter.classList.toggle("has-links", links.length > 0);
  submitBtn.disabled = links.length === 0;
  if (links.length > 0) {
    formError.classList.add("hidden");
  }
}

function updateAudioHint() {
  if (removeAudioToggle.checked) {
    audioModeHint.textContent =
      "Aktif: Audio dibuang sepenuhnya tanpa menjejaskan kualiti video.";
  } else {
    audioModeHint.textContent =
      "Tidak aktif: Video dimuat turun tanpa watermark tetapi mengekalkan sound asal.";
  }
}

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (data.status === "ok" && data.ffmpeg_available) {
      healthPill.className = "health-pill ready";
      healthText.textContent = "Sedia (FFmpeg & yt-dlp aktif)";
    } else {
      healthPill.className = "health-pill error";
      healthText.textContent = "FFmpeg tidak ditemui";
    }
  } catch {
    healthPill.className = "health-pill error";
    healthText.textContent = "Pelayan tidak aktif";
  }
}

function renderStatusBadge(item) {
  switch (item.status) {
    case "queued":
      return `<span class="badge">Menunggu</span>`;
    case "downloading":
      return `<span class="badge badge-working"><span class="spinner"></span> Memuat turun...</span>`;
    case "removing_audio":
      return `<span class="badge badge-working"><span class="spinner"></span> Membuang sound...</span>`;
    case "processing":
      return `<span class="badge badge-working"><span class="spinner"></span> Memproses...</span>`;
    case "done":
      return `<span class="badge badge-done">Selesai</span>`;
    case "error":
      return `<span class="badge badge-error">Gagal</span>`;
    default:
      return `<span class="badge">${escapeHtml(item.status)}</span>`;
  }
}

function renderVideoCard(item, { isHistory = false } = {}) {
  const title = item.title || "Video TikTok";
  const author = item.author ? `@${item.author}` : "";
  const thumbHtml = item.thumbnail
    ? `<img src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy" />`
    : `<span>MP4</span>`;

  const sizeText = formatBytes(item.file_size);
  const audioBadge = item.remove_audio
    ? `<span class="badge badge-muted">Tanpa Sound</span>`
    : `<span class="badge">Dengan Sound</span>`;

  const doneBadges =
    item.status === "done" || isHistory
      ? `<span class="badge badge-clean">Tanpa Watermark</span>${audioBadge}${
          sizeText ? `<span class="badge">${sizeText}</span>` : ""
        }`
      : "";

  const statusHtml = isHistory ? "" : renderStatusBadge(item);
  const errorHtml =
    item.status === "error" && item.message
      ? `<div class="error-msg">${escapeHtml(item.message)}</div>`
      : "";

  let actionsHtml = "";
  if (item.status === "done" || isHistory) {
    const safeTitle = escapeHtml(title);
    const safeMeta = escapeHtml(
      [author, item.remove_audio ? "Tanpa Sound" : "Dengan Sound", sizeText]
        .filter(Boolean)
        .join(" • ")
    );
    actionsHtml = `
      <button type="button" class="btn btn-ghost btn-sm preview-btn"
        data-url="${escapeHtml(item.preview_url)}"
        data-download="${escapeHtml(item.download_url)}"
        data-title="${safeTitle}"
        data-meta="${safeMeta}">
        Preview
      </button>
      <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}">Muat Turun</a>
      ${
        isHistory
          ? `<button type="button" class="btn btn-danger-ghost btn-sm delete-btn" data-filename="${escapeHtml(
              item.filename
            )}">Padam</button>`
          : ""
      }
    `;
  }

  return `
    <div class="video-item">
      <div class="video-thumb">${thumbHtml}</div>
      <div class="video-info">
        <div class="video-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
        <div class="video-url">${author ? `${escapeHtml(author)} • ` : ""}${escapeHtml(item.url)}</div>
        <div class="badges">
          ${statusHtml}
          ${doneBadges}
        </div>
        ${errorHtml}
      </div>
      <div class="video-actions">${actionsHtml}</div>
    </div>
  `;
}

function renderBatch(job) {
  batchSection.classList.remove("hidden");
  const finished = job.done_count + job.error_count;
  const pct = job.total > 0 ? Math.round((finished / job.total) * 100) : 0;
  batchProgressBar.style.width = `${pct}%`;

  if (job.completed) {
    batchSummary.textContent = `Selesai: ${job.done_count} berjaya${
      job.error_count > 0 ? `, ${job.error_count} gagal` : ""
    } daripada ${job.total} video.`;
  } else {
    batchSummary.textContent = `Memproses ${finished} / ${job.total} video...`;
  }

  if (job.done_count >= 2 || (job.completed && job.done_count >= 1 && job.total > 1)) {
    downloadZipBtn.href = `/api/jobs/${job.job_id}/zip`;
    downloadZipBtn.classList.remove("hidden");
  } else {
    downloadZipBtn.classList.add("hidden");
  }

  batchList.innerHTML = job.items
    .map((item) => renderVideoCard(item, { isHistory: false }))
    .join("");
}

async function pollJob(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) return;
    const job = await res.json();
    renderBatch(job);

    if (job.completed) {
      clearInterval(activePollTimer);
      activePollTimer = null;
      submitBtn.disabled = extractLinks(urlInput.value).length === 0;
      loadHistory();
    }
  } catch (err) {
    console.error("Poll error:", err);
  }
}

async function startBatch() {
  const links = extractLinks(urlInput.value);
  if (links.length === 0) {
    formError.textContent = "Sila masukkan sekurang-kurangnya satu pautan TikTok yang sah.";
    formError.classList.remove("hidden");
    return;
  }

  formError.classList.add("hidden");
  submitBtn.disabled = true;

  try {
    const res = await fetch("/api/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        urls: links,
        remove_audio: removeAudioToggle.checked,
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Gagal memulakan proses.");
    }

    renderBatch({ ...data, done_count: 0, error_count: 0 });

    if (activePollTimer) clearInterval(activePollTimer);
    activePollTimer = setInterval(() => pollJob(data.job_id), 1000);
    pollJob(data.job_id);
  } catch (err) {
    formError.textContent = err.message || "Ralat berlaku semasa menghantar permintaan.";
    formError.classList.remove("hidden");
    submitBtn.disabled = false;
  }
}

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    if (data.downloads_dir) {
      downloadsDirHint.textContent = `Folder simpanan: ${data.downloads_dir}`;
    }

    const items = data.items || [];
    if (items.length === 0) {
      historyList.innerHTML = `<div class="empty-state">Belum ada video yang dimuat turun lagi.</div>`;
      clearHistoryBtn.classList.add("hidden");
      return;
    }

    clearHistoryBtn.classList.remove("hidden");
    historyList.innerHTML = items
      .map((item) => renderVideoCard(item, { isHistory: true }))
      .join("");
  } catch (err) {
    console.error("History error:", err);
  }
}

function openPreview(url, downloadUrl, title, meta) {
  previewTitle.textContent = title || "Pratonton Video";
  previewMeta.textContent = meta || "";
  previewDownloadBtn.href = downloadUrl || url;
  previewVideo.src = url;
  previewModal.classList.remove("hidden");
  previewVideo.play().catch(() => {});
}

function closePreview() {
  previewVideo.pause();
  previewVideo.removeAttribute("src");
  previewVideo.load();
  previewModal.classList.add("hidden");
}

document.addEventListener("click", async (e) => {
  const previewBtn = e.target.closest(".preview-btn");
  if (previewBtn) {
    openPreview(
      previewBtn.dataset.url,
      previewBtn.dataset.download,
      previewBtn.dataset.title,
      previewBtn.dataset.meta
    );
    return;
  }

  const deleteBtn = e.target.closest(".delete-btn");
  if (deleteBtn) {
    const filename = deleteBtn.dataset.filename;
    if (!filename) return;
    await fetch(`/api/history/${encodeURIComponent(filename)}`, { method: "DELETE" });
    loadHistory();
  }
});

urlInput.addEventListener("input", updateLinkCount);
removeAudioToggle.addEventListener("change", updateAudioHint);
submitBtn.addEventListener("click", startBatch);
pasteBtn.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (!text) return;
    urlInput.value = urlInput.value.trim()
      ? `${urlInput.value.trim()}\n${text.trim()}`
      : text.trim();
    updateLinkCount();
  } catch {
    urlInput.focus();
    formError.textContent = "Sila tekan lama dalam kotak teks dan pilih Paste (browser menyekat akses papan keratan automatik pada HTTP).";
    formError.classList.remove("hidden");
  }
});
clearBtn.addEventListener("click", () => {
  urlInput.value = "";
  formError.classList.add("hidden");
  updateLinkCount();
  urlInput.focus();
});

clearHistoryBtn.addEventListener("click", async () => {
  if (!confirm("Padam semua video yang telah dimuat turun?")) return;
  await fetch("/api/history", { method: "DELETE" });
  loadHistory();
});

closeModalBtn.addEventListener("click", closePreview);
modalBackdrop.addEventListener("click", closePreview);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !previewModal.classList.contains("hidden")) {
    closePreview();
  }
});

checkHealth();
updateAudioHint();
updateLinkCount();
loadHistory();
