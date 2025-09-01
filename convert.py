#!/usr/bin/env python3
import argparse
import subprocess
import os
import sys
import glob

def run_cmd(cmd, silent=False):
    """Run a shell command. If silent=True, suppress stdout/stderr unless error."""
    try:
        if silent:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {' '.join(cmd)}", file=sys.stderr)
        sys.exit(e.returncode)

def extract_subs(infile, tmp_srt, clean_only):
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', infile,
        '-map', '0:s:m:language:jpn', '-c:s', 'srt', tmp_srt
    ]
    if not clean_only:
        cmd.insert(4, '-stats')  # add stats after '-loglevel error'
    run_cmd(cmd, silent=clean_only)


def clean_srt(tmp_srt, clean_srt):
    sed_expr = r"s/&lrm;//g; s/\xE2\x80\x8E//g; s/\{\\an[1-9]\}//g"
    cmd = ['sed', '-E', '-i', '', sed_expr, tmp_srt]
    run_cmd(cmd)
    os.replace(tmp_srt, clean_srt)


def embed_subs(infile, clean_srt, out_mp4):
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-stats',
        '-i', infile, '-i', clean_srt,
        '-map', '0:v', '-map', '0:a', '-map', '1:s',
        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
        '-c:s', 'mov_text', '-metadata:s:s:0', 'language=jpn',
        '-movflags', '+faststart', out_mp4
    ]
    run_cmd(cmd)


def main():
    parser = argparse.ArgumentParser(
        description='Extract, clean, and embed Japanese subtitles'
    )
    parser.add_argument('--clean-only', action='store_true', help='Only extract and clean the SRT, skip embedding')
    parser.add_argument('patterns', nargs='+', help='MKV files or glob patterns')
    args = parser.parse_args()

    # Expand glob patterns
    files = []
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
        extract_subs(infile, tmp_srt, args.clean_only)
        clean_srt(tmp_srt, clean_srt_file)
        print(f" • Cleaned subtitles → {clean_srt_file}")

        if not args.clean_only:
            print(f" • Embedding cleaned subs into {out_mp4}...")
            embed_subs(infile, clean_srt_file, out_mp4)
            print(f" → Done: {out_mp4}")

if __name__ == '__main__':
    main()
