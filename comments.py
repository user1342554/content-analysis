"""
Extract comments from TikTok and YouTube videos using yt-dlp.
Yields progress messages for the web UI.
"""

import json
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent / "tiktok_analysis"
TIKTOK_DIR = BASE_DIR / "tiktok_videos"
YOUTUBE_DIR = BASE_DIR / "youtube_videos"
COMMENTS_FILE = BASE_DIR / "comments.json"


def load_existing_comments():
    if COMMENTS_FILE.exists():
        try:
            with open(COMMENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_comments(comments):
    with open(COMMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)


def get_video_urls(platform):
    video_dir = TIKTOK_DIR if platform == "tiktok" else YOUTUBE_DIR
    videos = []
    if not video_dir.exists():
        return videos

    for info_file in video_dir.glob("*.info.json"):
        video_id = info_file.stem.replace(".info", "")
        if platform == "tiktok" and len(video_id) > 30:
            continue
        if platform == "youtube" and (video_id.startswith("UC") or video_id.startswith("@")):
            continue

        try:
            with open(info_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                url = data.get("webpage_url", "")
                title = data.get("title", "")
                if url:
                    videos.append({"id": video_id, "url": url, "title": title})
        except (json.JSONDecodeError, OSError):
            continue

    return videos


def extract_comments_for_video(url):
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-comments",
        "--no-write-info-json",
        "--dump-json",
        url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        raw_comments = data.get("comments", [])
        if not raw_comments:
            return []

        comments = []
        replies_map = {}

        for c in raw_comments:
            comment = {
                "id": c.get("id", ""),
                "author": c.get("author", ""),
                "author_id": c.get("author_id", ""),
                "text": c.get("text", ""),
                "likes": c.get("like_count", 0),
                "is_favorited": c.get("is_favorited", False),
                "timestamp": c.get("timestamp"),
                "time_text": c.get("_time_text", ""),
                "parent": c.get("parent", "root"),
                "replies": [],
            }

            if comment["parent"] == "root":
                comments.append(comment)
            else:
                parent_id = comment["parent"]
                if parent_id not in replies_map:
                    replies_map[parent_id] = []
                replies_map[parent_id].append(comment)

        for comment in comments:
            if comment["id"] in replies_map:
                comment["replies"] = replies_map[comment["id"]]

        return comments

    except subprocess.TimeoutExpired:
        return None
    except (json.JSONDecodeError, KeyError):
        return None


def extract_all_comments_streaming(force=False):
    """Extract comments for all downloaded videos. Yields log messages."""
    all_comments = load_existing_comments()
    yield f"{len(all_comments)} videos already have comments"

    for platform in ["tiktok", "youtube"]:
        videos = get_video_urls(platform)
        if not videos:
            continue

        to_process = [v for v in videos if v["id"] not in all_comments or force]

        if not to_process:
            yield f"All {platform} comments already extracted!"
            continue

        yield f"{platform}: {len(to_process)} videos need comment extraction"

        for i, video in enumerate(to_process, 1):
            title = video["title"][:50] or video["id"]
            yield f"[{i}/{len(to_process)}] {platform}: {title}"

            comments = extract_comments_for_video(video["url"])

            if comments is not None:
                all_comments[video["id"]] = {
                    "platform": platform,
                    "title": video["title"],
                    "url": video["url"],
                    "comment_count": len(comments),
                    "comments": comments,
                }
                save_comments(all_comments)
                total_replies = sum(len(c.get("replies", [])) for c in comments)
                yield f"  {len(comments)} comments, {total_replies} replies"
            else:
                all_comments[video["id"]] = {
                    "platform": platform,
                    "title": video["title"],
                    "url": video["url"],
                    "comment_count": 0,
                    "comments": [],
                    "error": True,
                }
                save_comments(all_comments)
                yield f"  Failed to extract comments"

    total = sum(v.get("comment_count", 0) for v in all_comments.values())
    yield f"Done! {len(all_comments)} videos, {total} total comments"
