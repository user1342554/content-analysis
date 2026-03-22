# content-analysis

downloads every video from a tiktok and youtube channel, transcribes them with whisper, and grabs all the comments. comes with a web ui to browse everything.

## what it does

- downloads all videos (audio) from tiktok and youtube channels using yt-dlp
- transcribes everything locally with openai whisper (gpu accelerated)
- extracts all comments with replies, likes, timestamps etc
- saves full metadata per video (views, likes, shares, resolution, etc) and channel stats (followers, totals)
- web ui to search across transcripts and comments, filter by platform, sort by whatever

## setup

you need python 3.10+, ffmpeg, and yt-dlp installed.

```
pip install flask
python app.py
```

open http://localhost:5000, go to setup, click install to get pytorch + whisper, paste your channel urls, hit run. thats it.

you can also run it from the command line:

```
python run_pipeline.py --tiktok-url https://www.tiktok.com/@whoever --youtube-url https://www.youtube.com/@whoever/videos
```

## how it works

everything gets saved to `tiktok_analysis/`. the pipeline skips stuff thats already downloaded/transcribed so you can rerun it to catch new uploads.

whisper large-v3 is the default model. needs ~10gb vram. if your gpu cant handle it pick a smaller model in setup.
