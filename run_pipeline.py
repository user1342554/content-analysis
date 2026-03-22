"""
CLI pipeline runner (alternative to the web UI).
Usage: python run_pipeline.py [download] [transcribe] [comments]
"""

import argparse
import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "tiktok_analysis" / "config.json"


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    parser = argparse.ArgumentParser(description="Content Pipeline CLI")
    parser.add_argument("steps", nargs="*", default=["all"],
                        choices=["all", "download", "transcribe", "comments"])
    parser.add_argument("--tiktok-url", help="TikTok channel URL")
    parser.add_argument("--youtube-url", help="YouTube channel URL")
    parser.add_argument("--model", default="large-v3", help="Whisper model")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = load_config()
    tiktok_url = args.tiktok_url or config.get("tiktok_url", "")
    youtube_url = args.youtube_url or config.get("youtube_url", "")
    model = args.model or config.get("whisper_model", "large-v3")

    steps = args.steps
    if "all" in steps:
        steps = ["download", "transcribe", "comments"]

    print("=" * 60)
    print("  Content Pipeline")
    print(f"  Steps: {', '.join(steps)}")
    print(f"  TikTok: {tiktok_url or '(not set)'}")
    print(f"  YouTube: {youtube_url or '(not set)'}")
    print("=" * 60)

    if "download" in steps:
        from downloader import download_platform
        if tiktok_url:
            for msg in download_platform("tiktok", tiktok_url):
                print(f"  {msg}")
        if youtube_url:
            for msg in download_platform("youtube", youtube_url):
                print(f"  {msg}")

    if "transcribe" in steps:
        from transcriber import transcribe_all_streaming
        for msg in transcribe_all_streaming(model_name=model, force=args.force):
            print(f"  {msg}")

    if "comments" in steps:
        from comments import extract_all_comments_streaming
        for msg in extract_all_comments_streaming(force=args.force):
            print(f"  {msg}")

    print("\n  Done! Run 'python app.py' to browse results.\n")


if __name__ == "__main__":
    main()
