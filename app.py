import os
import re
import shutil
import tempfile
from pathlib import Path

import requests
from flask import Flask, request, jsonify, send_file, render_template, after_this_request
import yt_dlp

app = Flask(__name__)

SUPPORTED_PATTERNS = {
    "tiktok": re.compile(r"tiktok\.com", re.I),
    "instagram": re.compile(r"instagram\.com", re.I),
}


def detect_platform(url: str):
    for name, pattern in SUPPORTED_PATTERNS.items():
        if pattern.search(url):
            return name
    return None


def base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 3,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }


def has_video(formats):
    return any((f.get("vcodec") not in (None, "none")) for f in (formats or []))


def extract_image_url(info):
    """Photo posts have no video formats. yt-dlp sometimes still exposes the
    photo directly via 'url'/'ext', otherwise fall back to the largest thumbnail."""
    ext = (info.get("ext") or "").lower()
    if info.get("url") and ext in ("jpg", "jpeg", "png", "webp"):
        return info["url"]
    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        best = max(thumbnails, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
        return best.get("url")
    return None


def fetch_instagram_preview(url):
    """yt-dlp refuses Instagram photo posts outright ('There is no video in
    this post'). Fall back to the page's own Open Graph tags, the same data
    that powers link previews in Messages/WhatsApp — no login needed for
    public posts."""
    try:
        resp = requests.get(url, headers={"User-Agent": base_opts()["user_agent"]}, timeout=15)
        resp.raise_for_status()
    except Exception:
        return None, None

    html = resp.text

    def og(prop):
        m = re.search(rf'property="og:{prop}"\s+content="([^"]*)"', html)
        if not m:
            m = re.search(rf'content="([^"]*)"\s+property="og:{prop}"', html)
        return m.group(1) if m else None

    return og("image"), (og("title") or og("description"))


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
        return jsonify({"error": "That doesn't look like a TikTok or Instagram link."}), 400

    ydl_opts = {**base_opts(), "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if platform == "instagram" and "no video" in str(exc).lower():
            image_url, title = fetch_instagram_preview(url)
            if image_url:
                return jsonify(
                    {
                        "platform": platform,
                        "title": title or "Instagram photo",
                        "thumbnail": image_url,
                        "duration": None,
                        "uploader": None,
                        "qualities": [],
                        "is_image": True,
                    }
                )
        return jsonify({"error": f"Couldn't read that link ({exc})"}), 422

    formats = info.get("formats") or []
    is_video = has_video(formats)
    is_image = not is_video and extract_image_url(info) is not None

    return jsonify(
        {
            "platform": platform,
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
            "qualities": build_quality_options(formats),
            "is_image": is_image,
        }
    )


def _download_image(url):
    image_url = None
    try:
        with yt_dlp.YoutubeDL({**base_opts(), "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        image_url = extract_image_url(info)
    except Exception as exc:
        if "no video" not in str(exc).lower():
            return jsonify({"error": f"Couldn't read that link ({exc})"}), 422
        # yt-dlp refuses photo posts outright — fall through to the scrape fallback below.

    if not image_url:
        image_url, _ = fetch_instagram_preview(url)

    if not image_url:
        return jsonify({"error": "No downloadable image found for this post."}), 422

    try:
        resp = requests.get(image_url, timeout=20, stream=True)
        resp.raise_for_status()
    except Exception as exc:
        return jsonify({"error": f"Image download failed ({exc})"}), 422

    content_type = resp.headers.get("Content-Type", "")
    ext = "png" if "png" in content_type else "webp" if "webp" in content_type else "jpg"

    tmpdir = tempfile.mkdtemp(prefix="clipgrab_img_")
    filepath = Path(tmpdir) / f"instagram_image.{ext}"
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)

    @after_this_request
    def cleanup(response):
        shutil.rmtree(tmpdir, ignore_errors=True)
        return response

    return send_file(filepath, as_attachment=True, download_name=filepath.name)


@app.route("/api/download", methods=["GET"])
def download():
    url = (request.args.get("url") or "").strip()
    mode = request.args.get("mode", "video")
    format_id = (request.args.get("format_id") or "").strip()

    if not url or not detect_platform(url):
        return jsonify({"error": "Missing or unsupported link."}), 400

    if mode == "image":
        return _download_image(url)

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
