import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

app = Flask(__name__)

DOWNLOAD_DIR = Path("/tmp/yt_downloads") if os.name != "nt" else Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True, parents=True)

progress_store: dict[str, dict] = {}


def cleanup_old_jobs():
    """Löscht Jobs die älter als 30 Minuten sind (Speicher sparen auf Server)."""
    while True:
        time.sleep(600)
        now = time.time()
        for job_dir in DOWNLOAD_DIR.iterdir():
            try:
                if job_dir.is_dir() and (now - job_dir.stat().st_mtime) > 1800:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    progress_store.pop(job_dir.name, None)
            except Exception:
                pass


threading.Thread(target=cleanup_old_jobs, daemon=True).start()


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)


def get_ydl_opts(fmt: str, job_id: str, output_path: Path) -> dict:
    def progress_hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            percent = int(downloaded / total * 100) if total else 0
            speed = d.get("speed", 0) or 0
            speed_mb = round(speed / 1_000_000, 2)
            progress_store[job_id] = {
                "status": "downloading",
                "percent": percent,
                "speed": f"{speed_mb} MB/s",
                "filename": d.get("filename", ""),
            }
        elif d["status"] == "finished":
            progress_store[job_id] = {"status": "processing", "percent": 99}

    outtmpl = str(output_path / "%(title)s.%(ext)s")

    # Bot-Detection umgehen: iOS/Android Client verwenden
    extractor_args = {"youtube": {"player_client": ["ios", "android", "web"]}}

    if fmt == "mp3":
        return {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "progress_hooks": [progress_hook],
            "extractor_args": extractor_args,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }
            ],
            "quiet": True,
        }
    else:
        return {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": outtmpl,
            "progress_hooks": [progress_hook],
            "extractor_args": extractor_args,
            "merge_output_format": "mp4",
            "quiet": True,
        }


def run_download(job_id: str, url: str, fmt: str):
    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        opts = get_ydl_opts(fmt, job_id, job_dir)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")
            ext = "mp3" if fmt == "mp3" else "mp4"
            filename = f"{sanitize_filename(title)}.{ext}"
            # find actual file
            files = list(job_dir.glob(f"*.{ext}"))
            actual_file = files[0].name if files else filename
        progress_store[job_id] = {
            "status": "done",
            "percent": 100,
            "filename": actual_file,
            "title": title,
        }
    except Exception as e:
        progress_store[job_id] = {"status": "error", "message": str(e)}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/info", methods=["POST"])
def get_info():
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"error": "Keine URL angegeben"}), 400
    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "extractor_args": {"youtube": {"player_client": ["ios", "android", "web"]}},
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return jsonify({
            "title": info.get("title", "Unbekannt"),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", ""),
            "view_count": info.get("view_count", 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    fmt = data.get("format", "mp4")
    if not url:
        return jsonify({"error": "Keine URL angegeben"}), 400
    if fmt not in ("mp3", "mp4"):
        return jsonify({"error": "Ungültiges Format"}), 400

    job_id = str(uuid.uuid4())
    progress_store[job_id] = {"status": "starting", "percent": 0}
    thread = threading.Thread(target=run_download, args=(job_id, url, fmt), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/progress/<job_id>")
def progress(job_id: str):
    def generate():
        while True:
            data = progress_store.get(job_id, {"status": "unknown"})
            yield f"data: {json.dumps(data)}\n\n"
            if data.get("status") in ("done", "error"):
                break
            time.sleep(0.5)
    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/file/<job_id>/<filename>")
def serve_file(job_id: str, filename: str):
    filepath = DOWNLOAD_DIR / job_id / filename
    if not filepath.exists():
        return "Datei nicht gefunden", 404

    def generate_and_cleanup():
        try:
            with open(filepath, "rb") as f:
                while chunk := f.read(8192):
                    yield chunk
        finally:
            # Datei nach dem Senden löschen (Speicher sparen)
            shutil.rmtree(DOWNLOAD_DIR / job_id, ignore_errors=True)
            progress_store.pop(job_id, None)

    ext = filename.rsplit(".", 1)[-1].lower()
    mime = "audio/mpeg" if ext == "mp3" else "video/mp4"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Length": str(filepath.stat().st_size),
    }
    return Response(stream_with_context(generate_and_cleanup()), mimetype=mime, headers=headers)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"YouTube Downloader läuft auf http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)
