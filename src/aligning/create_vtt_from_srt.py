#!/usr/bin/env python3
# **********************************************************
#
# @Author: Andreas Paepcke
# @Date:   2026-09-20 15:24:54
# @File:   /Users/paepcke/VSCodeWorkspaces/subtitle-alignment-cli/src/aligning/create_vtt_from_srt.py
# @Last Modified by:   Andreas Paepcke
# @Last Modified time: 2026-09-20 15:25:21
#
# **********************************************************

"""
Convert a .srt subtitle file to .vtt (WebVTT), for use in an HTML5 <track>
element.

WHY THIS IS NEEDED (read this before wondering why the browser doesn't
show the subtitle tracks already embedded in the .mp4):

  attach_srt_to_video.py embeds each language as a `mov_text` subtitle
  track inside the .mp4 container itself. Native media players (VLC,
  QuickTime, the OS-level player on iOS/Android) read those embedded
  tracks directly and list them in their own subtitle menu -- that part
  already works, and was verified earlier in this project.

  A browser's HTML5 <video> element is a different, much narrower
  consumer: it does NOT read embedded mov_text tracks out of the
  container at all, regardless of browser. The ONLY subtitle mechanism
  a web <video> element understands is an explicit <track> child element
  pointing at a separate WebVTT (.vtt) file served alongside the page.
  No <track>, no "CC" button in the browser's controls -- which is
  exactly the "Chrome doesn't make the subtitles obvious" symptom this
  fixes: Chrome isn't hiding them, it was never given them.

  This script converts each existing .srt (already produced by
  create_srt_file.py / create_translated_srt.py) into a sibling .vtt, so
  the same timing/text can be reused instead of regenerating it.

CONVERSION DETAILS (SRT -> WebVTT is a near-identical format):
  - WebVTT files must start with a literal "WEBVTT" line.
  - WebVTT timestamps use "." as the fractional-seconds separator instead
    of SRT's ",", e.g. 00:00:15.600 instead of 00:00:15,600. Everything
    else (cue numbering, arrow separator, cue text, blank line between
    cues) is compatible as-is.

USAGE
    python3 srt_to_vtt.py en.srt en.vtt
    python3 srt_to_vtt.py sv.srt sv.vtt
    # ...one call per language; or omit the output path to write
    # alongside the input with a .vtt extension:
    python3 srt_to_vtt.py en.srt
"""

import argparse
import re
from pathlib import Path

TIMESTAMP_COMMA_RE = re.compile(r"(\d{2}:\d{2}:\d{2}),(\d{3})")


def srt_to_vtt_text(srt_text: str) -> str:
    # WebVTT requires LF/CRLF-tolerant "WEBVTT" as the very first line.
    body = TIMESTAMP_COMMA_RE.sub(r"\1.\2", srt_text.strip())
    return "WEBVTT\n\n" + body + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("srt", type=Path, help="Input .srt file.")
    ap.add_argument("vtt", type=Path, nargs="?", default=None,
                     help="Output .vtt file. Default: same name as --srt, "
                          "with a .vtt extension, next to it.")
    args = ap.parse_args()

    out_path = args.vtt or args.srt.with_suffix(".vtt")
    vtt_text = srt_to_vtt_text(args.srt.read_text(encoding="utf-8"))
    out_path.write_text(vtt_text, encoding="utf-8")
    print(f"Wrote {out_path.resolve()}")


if __name__ == "__main__":
    main()
