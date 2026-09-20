#!/usr/bin/env python3
# **********************************************************
#
# @Author: Andreas Paepcke
# @Date:   2026-09-20 15:24:54
# @File:   /Users/paepcke/VSCodeWorkspaces/subtitle-alignment-cli/src/aligning/create_vtt_from_srt.py
# @Last Modified by:   Andreas Paepcke
# @Last Modified time: 2026-09-20 16:07:46
#
# **********************************************************

"""
srt_to_vtt.py

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

WHY --line EXISTS (read this if captions jump around vertically, or sit
higher than you want):
  Left alone, every cue's vertical position defaults to WebVTT's
  `line:auto`. Chrome's `auto` behavior does two things that look like
  bugs but aren't: (1) it nudges captions UP whenever the native player
  control bar is visible, to avoid overlapping it, and back down once
  the controls fade out; (2) a cue whose text wraps to 2 lines (which
  happens more often in a language with longer compound words, e.g.
  German) gets pushed up further to make room, stacked upward from the
  same anchor. Combined, that produces 2-3 different observed heights
  for what should be one consistent caption position, and it settles
  higher than the true bottom of the frame by design (so a control bar
  never fully covers it) -- which also means it won't sit inside a
  black letterbox band at the very bottom of the picture without help.

  Passing --line pins every cue to one fixed, explicit position instead
  of `auto`, which removes both of those behaviors: no more jumping
  with the control bar, no more single- vs double-line height
  difference, and it can be set as far down (closer to 100%) as you
  want -- including into a black band baked into the video image
  itself. Trade-off: an explicit line position does NOT auto-avoid the
  control bar, so if you push it very close to 100% the native seek
  bar may overlap the caption while the viewer is hovering the video.
  90-92% is usually low enough to look "way down there" while still
  clearing the control bar; try a few values against your actual video.

USAGE
    python3 srt_to_vtt.py en.srt en.vtt --line 90%
    python3 srt_to_vtt.py sv.srt sv.vtt --line 90%
    # ...one call per language, using the SAME --line value for all of
    # them so every language sits at the same height; or omit the
    # output path to write alongside the input with a .vtt extension:
    python3 srt_to_vtt.py en.srt --line 90%
    # Omit --line entirely to keep the old default (line:auto) behavior.
"""

import argparse
import re
from pathlib import Path

TIMESTAMP_COMMA_RE = re.compile(r"(\d{2}:\d{2}:\d{2}),(\d{3})")
CUE_TIMING_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3})\s*$", re.MULTILINE
)


def srt_to_vtt_text(srt_text: str, line: str | None) -> str:
    # WebVTT requires "WEBVTT" as the very first line.
    body = TIMESTAMP_COMMA_RE.sub(r"\1.\2", srt_text.strip())
    if line:
        # Append a `line:<value>` cue setting to every cue timing line,
        # e.g. "00:00:01.000 --> 00:00:03.000" -> "... --> ... line:90%".
        body = CUE_TIMING_RE.sub(rf"\1 line:{line}", body)
    return "WEBVTT\n\n" + body + "\n"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("srt", type=Path, help="Input .srt file.")
    ap.add_argument("vtt", type=Path, nargs="?", default=None,
                     help="Output .vtt file. Default: same name as --srt, "
                          "with a .vtt extension, next to it.")
    ap.add_argument("--line", default=None,
                     help="Fixed vertical cue position to bake into every "
                          "cue, e.g. '90%%' (percent DOWN from the top of "
                          "the video frame). Omit to keep WebVTT's default "
                          "'line:auto' behavior -- see the module "
                          "docstring for why that jitters and sits higher "
                          "than expected.")
    args = ap.parse_args()

    out_path = args.vtt or args.srt.with_suffix(".vtt")
    vtt_text = srt_to_vtt_text(args.srt.read_text(encoding="utf-8"), args.line)
    out_path.write_text(vtt_text, encoding="utf-8")
    print(f"Wrote {out_path.resolve()}")


if __name__ == "__main__":
    main()
