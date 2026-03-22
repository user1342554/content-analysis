"""
Transcribe audio files using OpenAI Whisper (local, GPU-accelerated).
Yields progress messages for the web UI.
"""

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent / "tiktok_analysis"
TIKTOK_DIR = BASE_DIR / "tiktok_videos"
YOUTUBE_DIR = BASE_DIR / "youtube_videos"
TRANSCRIPTS_FILE = BASE_DIR / "transcripts.json"

LANGUAGE = "de"


def load_existing_transcripts():
    transcripts = {}
    if TRANSCRIPTS_FILE.exists():
        try:
            with open(TRANSCRIPTS_FILE, "r", encoding="utf-8") as f:
                transcripts = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    partial_file = BASE_DIR / "transcripts_partial.json"
    if partial_file.exists():
        try:
            with open(partial_file, "r", encoding="utf-8") as f:
                partial = json.load(f)
                for vid_id, data in partial.items():
                    if vid_id not in transcripts:
                        if isinstance(data, dict) and "transcript" in data:
                            transcripts[vid_id] = {
                                "platform": data.get("platform", "tiktok"),
                                "title": data.get("title", ""),
                                "text": data["transcript"],
                                "segments": [],
                            }
                        else:
                            transcripts[vid_id] = data
        except (json.JSONDecodeError, OSError):
            pass

    return transcripts


def find_audio_file(directory, video_id):
    for ext in [".mp3", ".m4a", ".opus", ".webm", ".mp4"]:
        path = directory / f"{video_id}{ext}"
        if path.exists():
            return path
    return None


def get_video_title(directory, video_id):
    info_file = directory / f"{video_id}.info.json"
    if info_file.exists():
        try:
            with open(info_file, "r", encoding="utf-8") as f:
                return json.load(f).get("title", "")
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def save_transcripts(transcripts):
    with open(TRANSCRIPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(transcripts, f, ensure_ascii=False, indent=2)


def transcribe_all_streaming(model_name="large-v3", force=False):
    """Transcribe all audio files. Yields log messages."""
    try:
        import whisper
        import torch
    except ImportError:
        yield "ERROR: Whisper/PyTorch not installed. Go to Setup and click Install."
        return

    device = "cuda" if torch.cuda.is_available() else "cpu"
    yield f"Using device: {device} ({'GPU accelerated' if device == 'cuda' else 'CPU - will be slow'})"

    yield f"Loading Whisper model: {model_name}..."
    model = whisper.load_model(model_name, device=device)
    yield f"Model loaded!"

    transcripts = load_existing_transcripts()
    yield f"{len(transcripts)} existing transcripts found"

    # Collect files to process
    to_process = []
    for platform, video_dir in [("tiktok", TIKTOK_DIR), ("youtube", YOUTUBE_DIR)]:
        if not video_dir.exists():
            continue
        for info_file in video_dir.glob("*.info.json"):
            video_id = info_file.stem.replace(".info", "")
            if platform == "tiktok" and len(video_id) > 30:
                continue
            if platform == "youtube" and (video_id.startswith("UC") or video_id.startswith("@")):
                continue
            if video_id in transcripts and not force:
                continue
            audio_file = find_audio_file(video_dir, video_id)
            if audio_file:
                to_process.append((platform, video_id, audio_file, video_dir))

    if not to_process:
        yield "Nothing new to transcribe!"
        save_transcripts(transcripts)
        return

    yield f"{len(to_process)} files to transcribe"

    for i, (platform, video_id, audio_file, video_dir) in enumerate(to_process, 1):
        title = get_video_title(video_dir, video_id)
        display = title[:60] if title else video_id
        yield f"[{i}/{len(to_process)}] {platform}: {display}"

        try:
            result = model.transcribe(str(audio_file), language=LANGUAGE, verbose=False)

            segments = []
            for seg in result.get("segments", []):
                segments.append({
                    "start": round(seg["start"], 2),
                    "end": round(seg["end"], 2),
                    "text": seg["text"].strip(),
                })

            transcripts[video_id] = {
                "platform": platform,
                "title": title,
                "text": result["text"].strip(),
                "segments": segments,
                "language": result.get("language", LANGUAGE),
            }

            save_transcripts(transcripts)
            yield f"  Done ({len(segments)} segments, {len(result['text'])} chars)"

        except Exception as e:
            yield f"  ERROR: {e}"
            continue

    yield f"Transcription complete! {len(transcripts)} total transcripts"
