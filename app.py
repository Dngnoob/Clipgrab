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

COOKIE_SOURCE = "/etc/secrets/cookies.txt"
COOKIE_FILE = "/tmp/cookies.txt"


def detect_platform(url: str):
    for name, pattern in SUPPORTED_PATTERNS.items():
        if pattern.search(url):
            return name
    return None


def ensure_writable_cookiefile():
    """yt-dlp writes session updates back to the cookiefile it's given.
    Render's secret files are read-only, so copy to /tmp once and use that."""
    if os.path.exists(COOKIE_FILE):
        return True
    if os.path.exists(COOKIE_SOURCE):
        try:
            shutil.copyfile(COOKIE_SOURCE, COOKIE_FILE)
            return True
        except IOError:
            return False
    return False


def base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 3,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if ensure_writable_cookiefile():
        opts["cookiefile"] = COOKIE_FILE
    return opts


def build_quality_options(formats):
    """Collapse yt-dlp's raw format list into one best entry per resolution."""
    best_by_height = {}
    for f in formats:
        height = f.get("height")
        vcodec = f.get("vcodec")
        if not height or vcodec in (None, "none"):
            continue
        tbr = f.get("tbr") or 0
        existing = best_by_height.get(height)
        if existing is None or tbr > existing["_tbr"]:
            best_by_height[height] = {
                "height": height,
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "_tbr": tbr,
            }

    options = sorted(best_by_height.values(), key=lambda x: x["height"], reverse=True)[:6]
    for o in options:
        o.pop("_tbr", None)
    return options


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

    ydl_opts = {**base_opts(), "skip_download": True}
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
            "qualities": build_quality_options(info.get("formats") or []),
        }
    )


@app.route("/api/download", methods=["GET"])
def download():
    url = (request.args.get("url") or "").strip()
    mode = request.args.get("mode", "video")
    format_id = (request.args.get("format_id") or "").strip()

    if not url or not detect_platform(url):
        return jsonify({"error": "Missing or unsupported link."}), 400

    tmpdir = tempfile.mkdtemp(prefix="clipgrab_")
    outtmpl = str(Path(tmpdir) / "%(title).80s.%(ext)s")

    if mode == "audio":
        ydl_opts = {
            **base_opts(),
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
        }
    else:
        if format_id:
            # Try muxing the chosen resolution with best audio; if that specific
            # format already has audio or can't be merged, fall back gracefully.
            fmt = f"{format_id}+bestaudio/{format_id}/best"
        else:
            fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        ydl_opts = {
            **base_opts(),
            "format": fmt,
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
