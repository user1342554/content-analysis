"""
Content Browser — self-contained web app.
Install dependencies, enter channel URLs, download/transcribe/extract comments, browse results.
All from the browser.
"""

import json
import subprocess
import sys
import threading
import time
import queue
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

app = Flask(__name__)

BASE_DIR = Path(__file__).parent / "tiktok_analysis"
CONFIG_FILE = BASE_DIR / "config.json"

# Global pipeline state
pipeline_state = {
    "running": False,
    "step": "",
    "progress": "",
    "log": [],
    "error": None,
}
log_queue = queue.Queue()


# ── Config ──────────────────────────────────────────────────────────

def load_config():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"tiktok_url": "", "youtube_url": "", "whisper_model": "large-v3"}


def save_config(cfg):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── Data loading helpers ────────────────────────────────────────────

def load_json(filename):
    path = BASE_DIR / filename
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {} if "transcripts" in filename or "comments" in filename else []


def get_all_videos():
    videos = []
    for name in ["tiktok_metadata.json", "youtube_metadata.json"]:
        data = load_json(name)
        if data:
            videos.extend(data)
    if not videos:
        # Fallback to old format
        old = load_json("video_metadata.json")
        if old:
            for v in old:
                v.setdefault("platform", "tiktok")
            videos.extend(old)
        yt = load_json("youtube_list.json")
        if yt:
            for v in yt:
                videos.append({
                    "id": v.get("id", ""),
                    "platform": "youtube",
                    "title": v.get("title", ""),
                    "description": v.get("description", ""),
                    "duration": v.get("duration", 0),
                    "duration_string": v.get("duration_string", ""),
                    "upload_date": v.get("upload_date", ""),
                    "views": v.get("view_count", 0),
                    "likes": v.get("like_count", 0),
                    "comments": v.get("comment_count", 0),
                    "url": v.get("webpage_url", v.get("url", "")),
                })
    return videos


# ── Jinja filters ───────────────────────────────────────────────────

def format_number(n):
    if n is None:
        return "0"
    n = int(n) if n else 0
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def format_date(date_str):
    if not date_str or len(str(date_str)) < 8:
        return str(date_str) if date_str else ""
    s = str(date_str)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def format_duration(seconds):
    if not seconds:
        return ""
    # If already a string like "1:14" or "13:16", return as-is
    if isinstance(seconds, str):
        if ":" in seconds:
            return seconds
        try:
            seconds = float(seconds)
        except ValueError:
            return seconds
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds//3600}:{(seconds%3600)//60:02d}:{seconds%60:02d}"
    return f"{seconds//60}:{seconds%60:02d}"


app.jinja_env.filters["fnum"] = format_number
app.jinja_env.filters["fdate"] = format_date
app.jinja_env.filters["fdur"] = format_duration


# ── Dependency checks ───────────────────────────────────────────────

def check_deps():
    deps = {}
    # yt-dlp
    try:
        r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
        deps["yt_dlp"] = {"installed": True, "version": r.stdout.strip()}
    except Exception:
        deps["yt_dlp"] = {"installed": False, "version": ""}

    # torch
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import torch; print(torch.__version__); print(torch.cuda.is_available())"],
            capture_output=True, text=True, timeout=30
        )
        lines = r.stdout.strip().split("\n")
        deps["torch"] = {
            "installed": r.returncode == 0,
            "version": lines[0] if lines else "",
            "cuda": lines[1] == "True" if len(lines) > 1 else False,
        }
    except Exception:
        deps["torch"] = {"installed": False, "version": "", "cuda": False}

    # whisper
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import whisper; print(whisper.__version__)"],
            capture_output=True, text=True, timeout=15
        )
        deps["whisper"] = {"installed": r.returncode == 0, "version": r.stdout.strip()}
    except Exception:
        deps["whisper"] = {"installed": False, "version": ""}

    # ffmpeg
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        first_line = r.stdout.split("\n")[0] if r.stdout else ""
        deps["ffmpeg"] = {"installed": True, "version": first_line[:60]}
    except Exception:
        deps["ffmpeg"] = {"installed": False, "version": ""}

    return deps


# ── Routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    config = load_config()
    # If no URLs configured, redirect to setup
    if not config.get("tiktok_url") and not config.get("youtube_url"):
        videos = get_all_videos()
        if not videos:
            return render_template("setup.html", config=config, deps=check_deps(), tab="setup")

    videos = get_all_videos()
    transcripts = load_json("transcripts.json")
    comments_data = load_json("comments.json")

    for v in videos:
        vid_id = v.get("id", "")
        v["has_transcript"] = vid_id in transcripts
        v["has_comments"] = vid_id in comments_data and comments_data[vid_id].get("comment_count", 0) > 0
        v["fetched_comments"] = comments_data.get(vid_id, {}).get("comment_count", 0)

    platform = request.args.get("platform", "all")
    if platform != "all":
        videos = [v for v in videos if v.get("platform") == platform]

    search = request.args.get("q", "").strip().lower()
    if search:
        filtered = []
        for v in videos:
            vid_id = v.get("id", "")
            if search in v.get("title", "").lower() or search in v.get("description", "").lower():
                filtered.append(v)
                continue
            if vid_id in transcripts and search in transcripts[vid_id].get("text", "").lower():
                v["_search_match"] = "transcript"
                filtered.append(v)
                continue
            if vid_id in comments_data:
                for c in comments_data[vid_id].get("comments", []):
                    if search in c.get("text", "").lower():
                        v["_search_match"] = "comments"
                        filtered.append(v)
                        break
        videos = filtered

    sort = request.args.get("sort", "date")
    reverse = request.args.get("order", "desc") == "desc"
    sort_keys = {
        "date": lambda v: v.get("upload_date", ""),
        "views": lambda v: v.get("views", 0) or 0,
        "likes": lambda v: v.get("likes", 0) or 0,
        "comments": lambda v: v.get("comments", 0) or 0,
        "duration": lambda v: v.get("duration", 0) or 0,
    }
    videos.sort(key=sort_keys.get(sort, sort_keys["date"]), reverse=reverse)

    all_vids = get_all_videos()
    stats = {
        "total": len(all_vids),
        "tiktok": len([v for v in all_vids if v.get("platform") == "tiktok"]),
        "youtube": len([v for v in all_vids if v.get("platform") == "youtube"]),
        "transcribed": len(transcripts),
        "with_comments": len([v for v in comments_data.values() if v.get("comment_count", 0) > 0]),
    }

    return render_template(
        "index.html", videos=videos, stats=stats, config=config,
        platform=platform, search=search, sort=sort,
        order="desc" if reverse else "asc",
    )


@app.route("/video/<video_id>")
def video_detail(video_id):
    videos = get_all_videos()
    video = next((v for v in videos if v.get("id") == video_id), None)
    if video is None:
        return "Video not found", 404

    transcript = load_json("transcripts.json").get(video_id, {})
    video_comments = load_json("comments.json").get(video_id, {})

    return render_template("video.html", video=video, transcript=transcript, comments=video_comments)


@app.route("/setup")
def setup_page():
    config = load_config()
    deps = check_deps()
    return render_template("setup.html", config=config, deps=deps, tab="setup")


# ── API endpoints ───────────────────────────────────────────────────

@app.route("/api/save-config", methods=["POST"])
def api_save_config():
    data = request.get_json()
    config = load_config()
    if "tiktok_url" in data:
        config["tiktok_url"] = data["tiktok_url"].strip()
    if "youtube_url" in data:
        config["youtube_url"] = data["youtube_url"].strip()
    if "whisper_model" in data:
        config["whisper_model"] = data["whisper_model"].strip()
    save_config(config)
    return jsonify({"ok": True})


@app.route("/api/check-deps")
def api_check_deps():
    return jsonify(check_deps())


@app.route("/api/install", methods=["POST"])
def api_install():
    """Install Whisper + PyTorch via pip. Streams output via SSE."""
    data = request.get_json() or {}
    package = data.get("package", "all")

    def generate():
        if package in ("torch", "all"):
            yield f"data: Installing PyTorch with CUDA support...\n\n"
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install",
                 "torch", "torchvision", "torchaudio",
                 "--index-url", "https://download.pytorch.org/whl/cu121"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1
            )
            for line in proc.stdout:
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()
            if proc.returncode != 0:
                yield f"data: ERROR: PyTorch installation failed (exit code {proc.returncode})\n\n"
            else:
                yield f"data: PyTorch installed successfully!\n\n"

        if package in ("whisper", "all"):
            yield f"data: Installing openai-whisper...\n\n"
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "openai-whisper"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1
            )
            for line in proc.stdout:
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()
            if proc.returncode != 0:
                yield f"data: ERROR: Whisper installation failed (exit code {proc.returncode})\n\n"
            else:
                yield f"data: Whisper installed successfully!\n\n"

        if package == "yt-dlp":
            yield f"data: Installing yt-dlp...\n\n"
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "yt-dlp"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1
            )
            for line in proc.stdout:
                yield f"data: {line.rstrip()}\n\n"
            proc.wait()
            if proc.returncode != 0:
                yield f"data: ERROR: yt-dlp installation failed\n\n"
            else:
                yield f"data: yt-dlp installed successfully!\n\n"

        yield f"data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    """Run the full pipeline. Streams progress via SSE."""
    if pipeline_state["running"]:
        return jsonify({"error": "Pipeline already running"}), 409

    data = request.get_json() or {}
    steps = data.get("steps", ["download", "transcribe", "comments"])
    config = load_config()

    def generate():
        pipeline_state["running"] = True
        pipeline_state["error"] = None

        try:
            tiktok_url = config.get("tiktok_url", "")
            youtube_url = config.get("youtube_url", "")
            whisper_model = config.get("whisper_model", "large-v3")

            if not tiktok_url and not youtube_url:
                yield f"data: {json.dumps({'type': 'error', 'message': 'No URLs configured. Go to Setup first.'})}\n\n"
                return

            # Step 1: Download
            if "download" in steps:
                yield f"data: {json.dumps({'type': 'step', 'step': 'download', 'message': 'Starting downloads...'})}\n\n"

                if tiktok_url:
                    yield f"data: {json.dumps({'type': 'log', 'message': f'Downloading TikTok: {tiktok_url}'})}\n\n"
                    for msg in _run_download("tiktok", tiktok_url):
                        yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"

                if youtube_url:
                    yield f"data: {json.dumps({'type': 'log', 'message': f'Downloading YouTube: {youtube_url}'})}\n\n"
                    for msg in _run_download("youtube", youtube_url):
                        yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"

                yield f"data: {json.dumps({'type': 'step_done', 'step': 'download', 'message': 'Downloads complete!'})}\n\n"

            # Step 2: Transcribe
            if "transcribe" in steps:
                yield f"data: {json.dumps({'type': 'step', 'step': 'transcribe', 'message': 'Starting transcription...'})}\n\n"

                for msg in _run_transcribe(whisper_model):
                    yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"

                yield f"data: {json.dumps({'type': 'step_done', 'step': 'transcribe', 'message': 'Transcription complete!'})}\n\n"

            # Step 3: Comments
            if "comments" in steps:
                yield f"data: {json.dumps({'type': 'step', 'step': 'comments', 'message': 'Extracting comments...'})}\n\n"

                for msg in _run_comments():
                    yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"

                yield f"data: {json.dumps({'type': 'step_done', 'step': 'comments', 'message': 'Comments extracted!'})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'message': 'Pipeline complete!'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            pipeline_state["running"] = False

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/pipeline-status")
def api_pipeline_status():
    return jsonify(pipeline_state)


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return jsonify([])

    transcripts = load_json("transcripts.json")
    comments_data = load_json("comments.json")
    videos = get_all_videos()
    results = []

    for v in videos:
        vid_id = v.get("id", "")
        matches = []

        if q in v.get("title", "").lower():
            matches.append({"type": "title", "text": v["title"]})

        if vid_id in transcripts:
            text = transcripts[vid_id].get("text", "")
            idx = text.lower().find(q)
            if idx >= 0:
                start = max(0, idx - 40)
                end = min(len(text), idx + len(q) + 40)
                snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
                matches.append({"type": "transcript", "text": snippet})

        if vid_id in comments_data:
            for c in comments_data[vid_id].get("comments", []):
                if q in c.get("text", "").lower():
                    matches.append({"type": "comment", "text": f"{c.get('author', '???')}: {c['text'][:100]}"})
                    break

        if matches:
            results.append({"id": vid_id, "title": v.get("title", ""), "platform": v.get("platform", ""), "matches": matches})
        if len(results) >= 20:
            break

    return jsonify(results)


# ── Pipeline runners (generators that yield log messages) ───────────

def _run_download(platform, url):
    from downloader import download_platform
    yield from download_platform(platform, url)


def _run_transcribe(model_name):
    from transcriber import transcribe_all_streaming
    yield from transcribe_all_streaming(model_name=model_name)


def _run_comments():
    from comments import extract_all_comments_streaming
    yield from extract_all_comments_streaming()


if __name__ == "__main__":
    print("\n  Content Browser")
    print("  http://localhost:5000\n")
    app.run(debug=True, port=5000, threaded=True)
