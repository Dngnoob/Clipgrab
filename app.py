import os
import re
import shutil
import tempfile
from pathlib import Path

from flask import Flask, request, jsonify, send_file, render_template, after_this_request
import yt_dlp

app = Flask(__name__)

SUPPORTED_PATTERNS = {
    "youtube": re.compile(r"(youtube\.com|youtu\.be)", re.I),
    "tiktok": re.compile(r"tiktok\.com", re.I),
    "instagram": re.compile(r"instagram\.com", re.I),
}


def detect_platform(url: str):
    for name, pattern in SUPPORTED_PATTERNS.items():
        if pattern.search(url):
            return name
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/extract", methods=["POST"])
def extract():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "Paste a link first."}), 400

    platform = detect_platform(url)
    if not platform:
        return jsonify({"error": "That doesn't look like a YouTube, TikTok, or Instagram link."}), 400

    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return jsonify({"error": f"Couldn't read that link ({exc})"}), 422

    return jsonify(
        {
            "platform": platform,
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
        }
    )


@app.route("/api/download", methods=["GET"])
def download():
    url = (request.args.get("url") or "").strip()
    mode = request.args.get("mode", "video")

    if not url or not detect_platform(url):
        return jsonify({"error": "Missing or unsupported link."}), 400

    tmpdir = tempfile.mkdtemp(prefix="clipgrab_")
    outtmpl = str(Path(tmpdir) / "%(title).80s.%(ext)s")

    if mode == "audio":
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
        }
    else:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": f"Download failed ({exc})"}), 422

    files = [f for f in Path(tmpdir).glob("*") if f.is_file()]
    if not files:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": "No file was produced."}), 500

    filepath = files[0]

    @after_this_request
    def cleanup(response):
        shutil.rmtree(tmpdir, ignore_errors=True)
        return response

    return send_file(filepath, as_attachment=True, download_name=filepath.name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
