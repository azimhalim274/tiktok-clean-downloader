@echo off
title TikTok Clean Downloader
cd /d "%~dp0"
echo Memulakan TikTok Clean Downloader...
echo Buka di PC    : http://127.0.0.1:8765
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /C:"IPv4"') do (
  echo Buka di Phone : http://%%a:8765
)
start "" "http://127.0.0.1:8765"
".venv\Scripts\uvicorn.exe" app:app --host 0.0.0.0 --port 8765
pause
