"""
Download TikTok and YouTube videos/audio using yt-dlp.
Accepts URLs dynamically. Yields progress messages for the web UI.
"""

import json
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent / "tiktok_analysis"
TIKTOK_DIR = BASE_DIR / "tiktok_videos"
YOUTUBE_DIR = BASE_DIR / "youtube_videos"


def ensure_dirs():
    TIKTOK_DIR.mkdir(parents=True, exist_ok=True)
    YOUTUBE_DIR.mkdir(parents=True, exist_ok=True)


def get_downloaded_ids(directory):
    ids = set()
    if not directory.exists():
        return ids
    for f in directory.iterdir():
        if f.suffix in (".mp3", ".mp4", ".m4a", ".webm", ".opus"):
            ids.add(f.stem)
    return ids


def download_platform(platform, url, audio_only=True):
    """Download all videos from a channel URL. Yields log messages."""
    ensure_dirs()
    video_dir = TIKTOK_DIR if platform == "tiktok" else YOUTUBE_DIR

    existing = get_downloaded_ids(video_dir)
    yield f"Found {len(existing)} existing {platform} files"

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--write-info-json",
        "--no-overwrites",
        "--output", str(video_dir / "%(id)s.%(ext)s"),
        "--ignore-errors",
        "--newline",
    ]

    if platform == "tiktok":
        cmd.extend(["--sleep-interval", "1", "--max-sleep-interval", "3"])
    else:
        cmd.extend(["--sleep-interval", "2", "--max-sleep-interval", "5"])
        if not audio_only:
            # Remove extract-audio flags for full video
            cmd = [c for c in cmd if c not in ("--extract-audio", "--audio-format", "mp3", "--audio-quality", "0")]

    if existing:
        archive_file = video_dir / ".downloaded_archive"
        prefix = "tiktok" if platform == "tiktok" else "youtube"
        with open(archive_file, "w") as f:
            for vid_id in existing:
                f.write(f"{prefix} {vid_id}\n")
        cmd.extend(["--download-archive", str(archive_file)])

    cmd.append(url)

    yield f"Running yt-dlp for {platform}..."

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in proc.stdout:
            line = line.rstrip()
            if line:
                # Filter to show meaningful lines
                if any(kw in line.lower() for kw in ["download", "extract", "already", "error", "warning", "deleting"]):
                    yield line[:200]

        proc.wait()

        if proc.returncode != 0:
            yield f"Warning: yt-dlp exited with code {proc.returncode} (some videos may have failed)"

    except FileNotFoundError:
        yield "ERROR: yt-dlp not found. Install it from the Setup page."
        return

    # Update metadata
    yield f"Updating {platform} metadata..."
    count = update_video_list(platform, video_dir)
    yield f"Done! {count} {platform} videos indexed"


def update_video_list(platform, video_dir):
    """Build a clean metadata list from info.json files."""
    videos = []
    for info_file in sorted(video_dir.glob("*.info.json")):
        stem = info_file.stem.replace(".info", "")

        # Skip channel-level info files
        if platform == "tiktok" and len(stem) > 30:
            continue
        if platform == "youtube" and (stem.startswith("UC") or stem.startswith("@")):
            continue

        try:
            with open(info_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        video_id = data.get("id", stem)

        entry = {
            "id": video_id,
            "platform": platform,
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "duration": data.get("duration", 0),
            "duration_string": data.get("duration_string", ""),
            "upload_date": data.get("upload_date", ""),
            "views": data.get("view_count", 0),
            "likes": data.get("like_count", 0),
            "comments": data.get("comment_count", 0),
            "url": data.get("webpage_url", ""),
        }

        if platform == "tiktok":
            entry["reposts"] = data.get("repost_count", 0)
            entry["track"] = data.get("track", "")
            entry["artist"] = data.get("artist", "")
        else:
            entry["channel"] = data.get("channel", "")

        audio_exts = [".mp3", ".m4a", ".opus", ".webm", ".mp4"]
        entry["has_audio"] = any((video_dir / f"{video_id}{ext}").exists() for ext in audio_exts)

        videos.append(entry)

    videos.sort(key=lambda v: v.get("upload_date", ""), reverse=True)
    for i, v in enumerate(videos, 1):
        v["num"] = i

    out_file = BASE_DIR / f"{platform}_metadata.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)

    return len(videos)
