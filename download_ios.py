"""
iPhone / iPad version of download.py, made to run inside the a-Shell app.

Differences from download.py:
  * Saves to ~/Documents/YouTube, which shows up in the Files app under "a-Shell".
  * Prefers H.264 video + AAC audio in an MP4, so files play in Files/Photos.
    (H.264 tops out at 1080p on YouTube; 4K/1440p are VP9/AV1 and are skipped.)
  * Downloads one URL at a time (iOS suspends background apps).
  * URLs can be passed as arguments, which makes it usable from Apple Shortcuts:
        python3 download_ios.py "https://youtu.be/..."
        python3 download_ios.py --audio "https://youtu.be/..."
        python3 download_ios.py --res 720 "https://youtu.be/..."
        python3 download_ios.py --ask "https://youtu.be/..."   (shows the menu)
  * With no URL, it offers the clipboard contents, then asks for input.

Setup in a-Shell:
    pip install -U yt-dlp
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from typing import List, Optional

try:
    from yt_dlp import YoutubeDL
except ImportError:
    print("❌ yt-dlp is not installed. In a-Shell run:  pip install -U yt-dlp")
    sys.exit(1)

MAX_RETRIES = 3
RETRY_DELAY = 2
DEFAULT_OUTPUT = os.path.join(os.path.expanduser("~"), "Documents", "YouTube")
RESOLUTIONS = {"1": None, "2": 1080, "3": 720, "4": 480, "5": 360}

URL_PATTERN = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/\S+", re.IGNORECASE)
VALID_PATH_MARKERS = ("/watch?", "/playlist?", "/shorts/", "/live/",
                      "/@", "/channel/", "/c/", "/user/", "youtu.be/")


def parse_urls(text: str) -> List[str]:
    """Pull every YouTube URL out of free text (shared text often has extra words)."""
    found = []
    for url in URL_PATTERN.findall(text or ""):
        url = url.rstrip(".,;)]}>\"'")
        if any(m in url for m in VALID_PATH_MARKERS) and url not in found:
            found.append(url)
    return found


def read_clipboard() -> str:
    """Read the clipboard via a-Shell's pbpaste (or macOS pbpaste). Empty if unavailable."""
    if not shutil.which("pbpaste"):
        return ""
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def build_format(audio_only: bool, max_res: Optional[int], ffmpeg: bool) -> str:
    """Format selector that prefers iPhone-playable codecs."""
    if audio_only:
        # m4a (AAC) plays natively on iOS and needs no conversion.
        return "bestaudio[ext=m4a]/bestaudio/best"

    h = f"[height<={max_res}]" if max_res else ""
    if not ffmpeg:
        # Without ffmpeg, only pre-merged files work (usually 360p).
        return f"best[ext=mp4]{h}/best{h}/best"

    return "/".join([
        f"bestvideo[vcodec^=avc1]{h}+bestaudio[ext=m4a]",   # H.264 + AAC (ideal)
        f"bestvideo[vcodec^=avc1]{h}+bestaudio",
        f"best[ext=mp4]{h}",                                 # pre-merged MP4
        f"bestvideo{h}+bestaudio",                           # last resort (may be VP9/AV1)
        "best",
    ])


def build_options(output_path: str, audio_only: bool, mp3: bool,
                  max_res: Optional[int], ffmpeg: bool) -> dict:
    opts = {
        "format": build_format(audio_only, max_res, ffmpeg),
        "ignoreerrors": True,
        "noplaylist": False,
        "retries": MAX_RETRIES,
        "fragment_retries": MAX_RETRIES,
        "concurrent_fragment_downloads": 1,
        "noprogress": False,
        "progress_with_newline": False,
        # Keep names short; iOS paths can get long with playlist folders.
        "trim_file_name": 120,
        # Same as download.py: lets yt-dlp fetch YouTube's challenge solver.
        # Only has an effect if a JavaScript runtime is available.
        "remote_components": ["ejs:github"],
        "outtmpl": {
            "default": os.path.join(output_path, "%(title)s [%(id)s].%(ext)s"),
            "pl_video": os.path.join(
                output_path, "%(playlist_title)s",
                "%(playlist_index)03d - %(title)s [%(id)s].%(ext)s"),
        },
        "postprocessors": [],
    }

    if audio_only and mp3:
        if ffmpeg:
            opts["postprocessors"].append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            })
        else:
            print("⚠️  ffmpeg not found, saving audio as M4A instead of MP3.")
    elif not audio_only and ffmpeg:
        opts["merge_output_format"] = "mp4"

    return opts


def describe(info: dict) -> str:
    """Short summary of what was picked, so quality problems are visible."""
    parts = []
    for f in info.get("requested_formats") or [info]:
        if f.get("vcodec") not in (None, "none"):
            parts.append(f"video {f.get('height', '?')}p {f.get('vcodec', '?').split('.')[0]}")
        if f.get("acodec") not in (None, "none"):
            parts.append(f"audio {f.get('acodec', '?').split('.')[0]}")
    return ", ".join(parts)


def download_one(url: str, opts: dict) -> dict:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            if info is None:
                return {"url": url, "ok": False, "count": 0,
                        "msg": "Could not read video info (private, removed, or blocked)."}
            if info.get("_type") == "playlist":
                n = sum(1 for e in info.get("entries") or [] if e)
                return {"url": url, "ok": n > 0, "count": n,
                        "msg": f"Playlist/channel '{info.get('title', '?')}': {n} item(s)"}
            return {"url": url, "ok": True, "count": 1,
                    "msg": f"'{info.get('title', '?')}' ({describe(info)})"}
        except Exception as error:  # network hiccups are common on mobile
            last_error = error
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * (2 ** (attempt - 1))
                print(f"⚠️  Attempt {attempt}/{MAX_RETRIES} failed: {str(error)[:120]}. Retrying in {wait}s...")
                time.sleep(wait)
    return {"url": url, "ok": False, "count": 0, "msg": f"Failed: {last_error}"}


def ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def interactive_urls() -> List[str]:
    clip = parse_urls(read_clipboard())
    if clip:
        print("📋 Found in clipboard:")
        for u in clip:
            print(f"   • {u}")
        if ask("Use these? [Y/n]: ", "y").lower().startswith("y"):
            return clip
    text = ask("Paste YouTube URL(s): ")
    return parse_urls(text)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="YouTube downloader for a-Shell on iOS")
    parser.add_argument("urls", nargs="*", help="YouTube URLs (videos, playlists, channels, shorts)")
    parser.add_argument("--audio", action="store_true", help="audio only (M4A)")
    parser.add_argument("--mp3", action="store_true", help="audio only, converted to MP3")
    parser.add_argument("--res", type=int, choices=[1080, 720, 480, 360], help="max video height")
    parser.add_argument("--out", default=DEFAULT_OUTPUT, help=f"output folder (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--list-formats", action="store_true", help="show available formats and exit")
    parser.add_argument("--ask", action="store_true",
                        help="ask video/audio + quality even when URLs are given (for Shortcuts)")
    args = parser.parse_args(argv)

    interactive = not args.urls or args.ask
    urls = parse_urls(" ".join(args.urls)) if args.urls else interactive_urls()
    if not urls and args.urls and args.ask:
        urls = interactive_urls()  # shared text had no link: fall back to clipboard/prompt
    if not urls:
        print("❌ No valid YouTube URLs found.")
        return 1

    if args.list_formats:
        with YoutubeDL({"listformats": True}) as ydl:
            ydl.extract_info(urls[0], download=False)
        return 0

    audio_only = args.audio or args.mp3
    max_res = args.res
    if interactive and not audio_only:
        choice = ask("\n1. Video (MP4)  2. Audio (M4A)  3. Audio (MP3)\nChoose [1]: ", "1")
        if choice in ("2", "3"):
            audio_only, args.mp3 = True, choice == "3"
        else:
            max_res = RESOLUTIONS.get(ask(
                "Max quality: 1. Best (≤1080p H.264)  2. 1080p  3. 720p  4. 480p  5. 360p\nChoose [1]: ",
                "1"))

    ffmpeg = has_ffmpeg()
    if not ffmpeg and not audio_only:
        print("⚠️  ffmpeg not found: only pre-merged files can be saved (usually 360p).")

    os.makedirs(args.out, exist_ok=True)
    opts = build_options(args.out, audio_only, args.mp3, max_res, ffmpeg)

    print(f"\n📁 Saving to: {args.out}")
    print(f"🎬 {len(urls)} URL(s), one at a time\n" + "-" * 40)

    results = []
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] {url}")
        result = download_one(url, opts)
        print(("✅ " if result["ok"] else "❌ ") + result["msg"])
        results.append(result)

    ok = sum(r["count"] for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    print("\n" + "=" * 40)
    print(f"✅ Downloaded: {ok}   ❌ Failed URLs: {len(failed)}")
    for r in failed:
        print(f"   • {r['url']}\n     {r['msg']}")
    if ok:
        print("📂 Open the Files app → On My iPhone → a-Shell → YouTube")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
