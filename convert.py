#!/usr/bin/env python3
import argparse
import subprocess
import os
import sys
import glob
import json
import shlex
from typing import Optional, Tuple, List

TEXT_SUB_CODECS = {
    "subrip",  # .srt
    "ass",
    "ssa",
    "mov_text",
    "webvtt",
}

BITMAP_SUB_CODECS = {
    "hdmv_pgs_subtitle",  # PGS .sup (common on Blu-ray/WEB)
    "dvd_subtitle",  # VobSub (idx/sub)
}


def run_cmd(cmd: List[str], silent: bool = False):
    """Run a shell command. If silent=True, suppress stdout/stderr unless error."""
    try:
        if not isinstance(cmd, list):
            raise TypeError("cmd must be a list of strings")
        if silent:
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(
            f"Error running command: {' '.join(shlex.quote(c) for c in cmd)}",
            file=sys.stderr,
        )
        sys.exit(e.returncode)


def ffprobe_streams(infile: str) -> List[dict]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        "s",
        infile,
    ]
    try:
        out = subprocess.check_output(cmd)
    except subprocess.CalledProcessError as e:
        print(f"Failed to run ffprobe on {infile}: {e}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(out.decode("utf-8"))
    return data.get("streams", [])


def is_japanese(tags: dict) -> bool:
    if not tags:
        return False
    lang = (tags.get("language") or tags.get("LANGUAGE") or "").lower()
    return lang in {"jpn", "ja"}


def title_contains_forced(title: Optional[str]) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(
        k in t for k in ["forced", "signs", "songs & signs", "songs/signs"]
    )  # common labels


def title_prefers_full(title: Optional[str]) -> int:
    """Return a score boost if the title looks like 'full' subs. Higher is better."""
    if not title:
        return 0
    t = title.lower()
    score = 0
    if "full" in t:
        score += 3
    if "sdh" in t or "hearing" in t:
        score += 1
    # Avoid anything that hints forced
    if "forced" in t or "sign" in t:
        score -= 5
    return score


def pick_best_jpn_text_sub(infile: str) -> Tuple[Optional[int], str]:
    """
    Return (stream_index, reason).
    Picks the best *text* Japanese subtitle stream, preferring non-forced ("full").
    Falls back to forced only if no full/text track exists. Returns (None, reason) if none.
    """
    streams = ffprobe_streams(infile)
    if not streams:
        return None, "No subtitle streams found"

    jpn_streams = [s for s in streams if is_japanese(s.get("tags", {}))]
    if not jpn_streams:
        return None, "No Japanese subtitle streams found"

    # Partition text vs bitmap
    text_jpn = []
    bitmap_jpn = []
    for s in jpn_streams:
        codec = (s.get("codec_name") or "").lower()
        if codec in TEXT_SUB_CODECS:
            text_jpn.append(s)
        elif codec in BITMAP_SUB_CODECS:
            bitmap_jpn.append(s)

    if not text_jpn:
        if bitmap_jpn:
            return None, (
                "Only image-based Japanese subtitles found (e.g., PGS). "
                "Cannot convert to .srt without OCR."
            )
        return None, "No text-based Japanese subtitles found"

    # Score text subs: prefer non-forced, titles that look like full, default disposition, then lowest index
    def score_stream(s: dict) -> Tuple[int, int, int]:
        disp = s.get("disposition", {}) or {}
        forced = int(disp.get("forced", 0))
        default = int(disp.get("default", 0))
        title = (s.get("tags", {}) or {}).get("title")
        base = 100  # base to keep positive
        base += title_prefers_full(title)
        base += 5 if default else 0
        base += 0 if forced else 10  # prefer non-forced
        # Lower actual ffmpeg stream index gets tiny boost (more primary)
        idx = int(s.get("index", 0))
        return (base, -idx, -forced)

    # Among text subs, attempt to filter out 'forced' labeled when a non-forced exists
    non_forced_text = [
        s
        for s in text_jpn
        if int((s.get("disposition", {}) or {}).get("forced", 0)) == 0
        and not title_contains_forced((s.get("tags", {}) or {}).get("title"))
    ]
    pool = non_forced_text if non_forced_text else text_jpn

    best = sorted(pool, key=score_stream, reverse=True)[0]
    return int(best.get("index")), "Selected Japanese full text subtitles"


def find_external_sub(infile: str) -> Optional[str]:
    """Check if an external subtitle file exists next to the video."""
    base, _ = os.path.splitext(infile)
    candidates = [
        f"{base}.ja.srt",
        f"{base}.jpn.srt",
        f"{base}.jp.srt",
        f"{base}.srt",
        f"{base}.ass",
        f"{base}.ssa",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def extract_subs(infile: str, tmp_srt: str, clean_only: bool):
    idx, reason = pick_best_jpn_text_sub(infile)
    if idx is None:
        # Try to find an external subtitle file
        ext_sub = find_external_sub(infile)
        if ext_sub:
            print(f" • Using external subtitle file: {ext_sub}")
            # Copy to tmp_srt so later cleaning logic works the same
            run_cmd(["cp", ext_sub, tmp_srt])
            return
        else:
            print(f" ! Skipping: {reason} (no external subs found)")
            sys.exit(1)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        infile,
        "-map",
        f"0:{idx}",  # map the exact ffmpeg stream index
        "-c:s",
        "srt",
        tmp_srt,
    ]
    if not clean_only:
        cmd.insert(4, "-stats")
    run_cmd(cmd, silent=clean_only)


def clean_srt(tmp_srt: str, clean_srt: str):
    sed_expr = r"s/&lrm;//g; s/\xE2\x80\x8E//g; s/\{\\an[1-9]\}//g"
    # macOS/BSD sed requires an argument to -i; empty string keeps inline without backup
    cmd = ["sed", "-E", "-i", "", sed_expr, tmp_srt]
    run_cmd(cmd)
    os.replace(tmp_srt, clean_srt)


def embed_subs(infile: str, clean_srt: str, out_mp4: str):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-i",
        infile,
        "-i",
        clean_srt,
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-map",
        "1:s",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        "language=jpn",
        "-movflags",
        "+faststart",
        out_mp4,
    ]
    run_cmd(cmd)


def main():
    parser = argparse.ArgumentParser(
        description="Extract, clean, and embed Japanese FULL subtitles when available"
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="Only extract and clean the SRT, skip embedding",
    )
    parser.add_argument("patterns", nargs="+", help="MKV files or glob patterns")
    args = parser.parse_args()

    # Expand glob patterns
    files: List[str] = []
    for pat in args.patterns:
        expanded = glob.glob(pat)
        if not expanded:
            print(f"Warning: pattern '{pat}' did not match any files.", file=sys.stderr)
        files.extend(expanded)

    if not files:
        print("No input files found.", file=sys.stderr)
        sys.exit(1)

    for infile in files:
        base, _ = os.path.splitext(infile)
        tmp_srt = f"{base}-ja.tmp.srt"
        clean_srt_file = f"{base}-ja.clean.srt"
        out_mp4 = f"{base}.mp4"

        print(f"Processing '{infile}'...")
        try:
            extract_subs(infile, tmp_srt, args.clean_only)
        except SystemExit as e:
            # extract_subs already printed a reason; move to next file
            continue
        clean_srt(tmp_srt, clean_srt_file)
        print(f" • Cleaned subtitles → {clean_srt_file}")

        if not args.clean_only:
            print(f" • Embedding cleaned subs into {out_mp4}...")
            embed_subs(infile, clean_srt_file, out_mp4)
            print(f" → Done: {out_mp4}")


if __name__ == "__main__":
    main()
