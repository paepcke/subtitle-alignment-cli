#!/usr/bin/env python3
# **********************************************************
#
# @Author: Andreas Paepcke
# @Date:   2026-09-18 09:37:40
# @File:   /Users/paepcke/VSCodeWorkspaces/subtitle-alignment-cli/src/attaching/attach_srt_to_video.py
# @Last Modified by:   Andreas Paepcke
# @Last Modified time: 2026-09-18 18:03:56
#
# **********************************************************

"""
Attach one or more .srt subtitle files to an .mp4 video as SOFT
(toggleable) subtitle tracks -- not burned into the picture. Audio and
video streams are stream-copied (no re-encoding), so this is fast and
lossless.

MP4 containers only support one subtitle codec: `mov_text` (a simple
timed-text format QuickTime/MP4 understands). ffmpeg transcodes each
.srt's text into that codec automatically; the video/audio themselves
are untouched (`-c copy`).

WHY ALL TRACKS ARE ATTACHED IN ONE RUN (read this if you're wondering
why this script doesn't let you add one language at a time onto a
previous output, the way an earlier version did):

  Two real ffmpeg gotchas, both silent (no warning, no error):

  1. Daisy-chaining loses track TITLES. If you attach English, then
     re-run pointing --video at that output to add Swedish, then
     re-run again to add Spanish, ffmpeg's mov demuxer does not
     reliably re-expose a subtitle track's `title` metadata when it
     re-reads a file it (or another tool) already muxed. Concretely:
     by the third run, English's and Swedish's titles are both gone
     -- even though their LANGUAGE tags survive fine (language lives
     in the track header; title lives in a separate metadata atom
     that doesn't round-trip through a second remux). This was
     verified directly: a title set on generation 1 is already gone
     by generation 2, let alone generation 3. Attaching every track
     in a single ffmpeg invocation sidesteps this entirely, because
     there is only ever one generation.

  2. A 2-letter language code (ISO 639-1, e.g. "es") is silently
     accepted by argparse but silently DROPPED by the mp4 muxer --
     MP4 track language requires the 3-letter ISO 639-2 code ("spa").
     No error, no warning; the track just ends up with no language
     tag at all. This script now checks for a 3-letter alphabetic
     code up front and refuses to run otherwise.

USAGE
    python3 attach_srt_to_video.py \\
        --video therapistTrainerTutorial_2_1.mp4 \\
        --srt en.srt --language eng --label English \\
        --srt sv.srt --language swe --label Swedish \\
        --srt es.srt --language spa --label Spanish \\
        --srt de.srt --language deu --label Deutsch \\
        --out therapistTrainerTutorial_2_1_subtitled.mp4

    --srt/--language are each given once per track, in matching order.
    --label is optional per track; pass it as many times as --srt to
    label every track, or omit it entirely to label none.

    Need to add a language later, after already shipping a subtitled
    file? Re-run this script from the ORIGINAL (no-subtitles) --video
    with the full set of --srt/--language/--label triples you want in
    the final file -- not by pointing --video at a previously
    subtitled output. That keeps every run a single generation, which
    is what avoids the title-loss bug above.
"""

import argparse
import re
import subprocess
from pathlib import Path

LANGUAGE_CODE_RE = re.compile(r"^[A-Za-z]{3}$")


def attach(video_path: Path, tracks: list[dict], out_path: Path):
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    for track in tracks:
        cmd += ["-i", str(track["srt"])]

    cmd += ["-map", "0"]  # video, audio, and any subtitle tracks already in --video
    for i in range(len(tracks)):
        cmd += ["-map", f"{i + 1}:s"]

    cmd += ["-c", "copy", "-c:s", "mov_text"]

    for i, track in enumerate(tracks):
        cmd += [f"-metadata:s:s:{i}", f"language={track['language']}"]
        if track["label"]:
            cmd += [f"-metadata:s:s:{i}", f"title={track['label']}"]

    cmd.append(str(out_path))

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--video", required=True, type=Path,
                     help=".mp4 to attach subtitle track(s) to. Use the "
                          "ORIGINAL, not-yet-subtitled video -- see the "
                          "module docstring for why.")
    ap.add_argument("--srt", required=True, type=Path, action="append",
                     help=".srt file to attach. Repeat once per language, "
                          "in the same order as --language (and --label, "
                          "if given).")
    ap.add_argument("--language", required=True, action="append",
                     help="ISO 639-2 (3-letter) language code for the "
                          "matching --srt, e.g. eng, swe, spa, deu. "
                          "2-letter codes (es, sv, ...) are rejected -- "
                          "the mp4 muxer silently drops those instead of "
                          "erroring, which is worse.")
    ap.add_argument("--label", action="append", default=[],
                     help="Human-readable track name shown in player "
                          "subtitle menus, e.g. 'Swedish'. Optional; if "
                          "given, pass it once per --srt/--language pair.")
    ap.add_argument("--out", required=True, type=Path,
                     help="Output .mp4 path. Must differ from --video.")
    args = ap.parse_args()

    if args.out.resolve() == args.video.resolve():
        raise SystemExit("--out must differ from --video.")

    if len(args.srt) != len(args.language):
        raise SystemExit(
            f"Got {len(args.srt)} --srt but {len(args.language)} --language "
            f"options -- pass exactly one --language per --srt, in order."
        )
    if args.label and len(args.label) != len(args.srt):
        raise SystemExit(
            f"Got {len(args.label)} --label but {len(args.srt)} --srt "
            f"options -- pass one --label per --srt (in order), or omit "
            f"--label entirely."
        )
    labels = args.label or [None] * len(args.srt)

    bad_codes = [lang for lang in args.language if not LANGUAGE_CODE_RE.match(lang)]
    if bad_codes:
        raise SystemExit(
            f"--language must be a 3-letter ISO 639-2 code (e.g. eng, swe, "
            f"spa, deu), not: {', '.join(bad_codes)}. A 2-letter ISO 639-1 "
            f"code (es, sv, de, ...) is silently dropped by the mp4 muxer "
            f"instead of erroring, so it's rejected here up front."
        )

    tracks = [
        {"srt": srt, "language": lang, "label": label}
        for srt, lang, label in zip(args.srt, args.language, labels)
    ]

    attach(args.video, tracks, args.out)
    print(f"\nWrote: {args.out.resolve()}")
    print(f"Tracks: " + ", ".join(
        f"{t['language']}" + (f" ({t['label']})" if t["label"] else "")
        for t in tracks
    ))


if __name__ == "__main__":
    main()
