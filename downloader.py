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

    # Extract channel stats
    yield f"Extracting {platform} channel stats..."
    stats = extract_channel_stats(platform, url, video_dir)
    if stats:
        yield f"Channel: {stats.get('name', '?')} — {stats.get('followers', '?')} followers"
    else:
        yield f"Could not extract channel stats (will use info from downloaded data)"


def extract_channel_stats(platform, url, video_dir):
    """Extract channel-level stats (followers, description, etc.) from info.json files."""
    channel_stats = {}

    # Look for channel-level info.json files
    for info_file in video_dir.glob("*.info.json"):
        stem = info_file.stem.replace(".info", "")
        is_channel = False
        if platform == "tiktok" and len(stem) > 30:
            is_channel = True
        if platform == "youtube" and (stem.startswith("UC") or stem.startswith("@")):
            is_channel = True

        if not is_channel:
            continue

        try:
            with open(info_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        channel_stats = {
            "platform": platform,
            "id": data.get("id", ""),
            "name": data.get("title", "") or data.get("channel", ""),
            "description": data.get("description", ""),
            "url": data.get("webpage_url", url),
            "followers": data.get("channel_follower_count", 0),
            "video_count": data.get("playlist_count", 0),
        }

        if platform == "youtube":
            channel_stats["channel_id"] = data.get("channel_id", "")
            channel_stats["channel_url"] = data.get("channel_url", "")
        break

    # If no channel info found, try fetching it
    if not channel_stats:
        try:
            cmd = [
                "yt-dlp", "--dump-json", "--playlist-items", "0",
                "--flat-playlist", url,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip().split("\n")[0])
                channel_stats = {
                    "platform": platform,
                    "id": data.get("id", ""),
                    "name": data.get("title", "") or data.get("channel", ""),
                    "description": data.get("description", ""),
                    "url": url,
                    "followers": data.get("channel_follower_count", 0),
                    "video_count": data.get("playlist_count", 0),
                }
        except Exception:
            pass

    # Also compute aggregate stats from video metadata
    videos_file = BASE_DIR / f"{platform}_metadata.json"
    if videos_file.exists():
        try:
            with open(videos_file, "r", encoding="utf-8") as f:
                videos = json.load(f)
            channel_stats["total_views"] = sum(v.get("views", 0) or 0 for v in videos)
            channel_stats["total_likes"] = sum(v.get("likes", 0) or 0 for v in videos)
            channel_stats["total_comments"] = sum(v.get("comments", 0) or 0 for v in videos)
            channel_stats["total_reposts"] = sum(v.get("reposts", 0) or 0 for v in videos)
            channel_stats["video_count"] = len(videos)
        except (json.JSONDecodeError, OSError):
            pass

    if channel_stats:
        out_file = BASE_DIR / f"{platform}_channel.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(channel_stats, f, ensure_ascii=False, indent=2)

    return channel_stats


def update_video_list(platform, video_dir):
    """Build a clean metadata list from info.json files with ALL available fields."""
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
            "fulltitle": data.get("fulltitle", ""),
            "description": data.get("description", ""),
            "duration": data.get("duration", 0),
            "duration_string": data.get("duration_string", ""),
            "upload_date": data.get("upload_date", ""),
            "timestamp": data.get("timestamp"),
            "views": data.get("view_count", 0),
            "likes": data.get("like_count", 0),
            "comments": data.get("comment_count", 0),
            "url": data.get("webpage_url", ""),
            "thumbnail": data.get("thumbnail", ""),
            "resolution": data.get("resolution", ""),
            "filesize": data.get("filesize", 0),
        }

        if platform == "tiktok":
            entry["reposts"] = data.get("repost_count", 0)
            entry["track"] = data.get("track", "")
            entry["artist"] = data.get("artist", "")
            entry["uploader"] = data.get("uploader", "")
            entry["uploader_id"] = data.get("uploader_id", "")
            entry["channel"] = data.get("channel", "")
        else:
            entry["channel"] = data.get("channel", "")
            entry["channel_id"] = data.get("channel_id", "")
            entry["categories"] = data.get("categories", [])
            entry["tags"] = data.get("tags", [])
            entry["availability"] = data.get("availability", "")

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
