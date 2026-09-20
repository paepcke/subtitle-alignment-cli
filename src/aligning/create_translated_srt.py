#!/usr/bin/env python3
# **********************************************************
#
# @Author: Andreas Paepcke
# @Date:   2026-09-18 16:41:49
# @File:   /Users/paepcke/VSCodeWorkspaces/subtitle-alignment-cli/src/aligning/create_translated_srt.py
# @Last Modified by:   Andreas Paepcke
# @Last Modified time: 2026-09-18 17:07:29
#
# **********************************************************

"""
create_translated_srt.py

Build a translated-language .srt for a section that has NO corresponding
audio in that language -- unlike create_srt_file.py's English pass, there
is nothing here to force-align against with WhisperX. The video's audio
track stays in the original language regardless of which subtitle track
a viewer picks.

TIMING METHOD (read this before trusting the output):
  Each section's real, already-known time window in the final video comes
  from the same clip-timing table extract_mp3_clips.py produced for the
  English pipeline (e.g. audio_files_and_timings.txt) -- so section
  START/END times are exact, not estimated.
  WITHIN a section's window, though, there is no way to know exactly when
  a Swedish reader would want each cue to appear, because no Swedish
  speech exists to time against. This script instead splits each
  section's translated text into subtitle-sized cues (see --max-chars),
  and distributes the section's real duration across those cues
  PROPORTIONAL TO EACH CUE'S CHARACTER COUNT. This is a reasonable,
  defensible placeholder -- not real speech timing. If particular cues
  look oddly paced once you preview the video, nudge them by hand in
  Aegisub; this script gets you a solid starting point, not a final cut.

INPUTS
  --clip-table         Same table format as create_srt_file.py uses
                        (e.g. audio_files_and_timings.txt). Optional:
                        defaults to `audio_files_and_timings.txt` in
                        this script's own directory.
  --translated-script   The translated text file, in the SAME block
                        format as script_v2_1.txt (a bare "sectionX"
                        label line, then that section's translated text,
                        blank line between blocks). Its blocks must be in
                        the same ORDER as the clip-table rows -- matched
                        by position, not by label text.
  --max-chars           Maximum characters per subtitle cue line.
                        Default: 34 (a little more headroom than the
                        32 used for English, since Swedish's compound
                        words tend to run longer).
  --language            Language code, used only for the printed log
                        and does not affect processing (no model is
                        loaded for this script). Default: "sv".

OUTPUT
  --out-srt             The translated .srt file.

  This script deliberately does NOT write a section-boundaries JSON.
  Those boundaries come from `audio_files_and_timings.txt` alone -- they
  don't depend on subtitle language or text at all -- so create_srt_file.py
  writing one, once, per project is the single source of truth. A
  per-language copy here would just be an identical duplicate under a
  different name (this is also why create_srt_file.py's own
  `--out-boundaries` output only needs regenerating if the underlying
  clip-timing table itself changes, not on every run).

USAGE
    python3 create_translated_srt.py \
        --translated-script script_v2_1_sv.txt \
        --out-srt sv.srt \
        --max-chars 34
    # (--clip-table takes its default above)
"""

import argparse
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. Parse the clip table (same format/regex as create_srt_file.py)
# ---------------------------------------------------------------------------
ROW_RE = re.compile(
    r"^\s*(\d+):(\d+\.\d+)\s*\|\s*([\d.]+)s\s*\|\s*\S+\s*\|\s*(.+?)\s*$"
)


def parse_clip_table(path: Path):
    rows = []
    for line in path.read_text().splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        mins, secs, dur, mp3_path = m.groups()
        rows.append({
            "start_seconds": int(mins) * 60 + float(secs),
            "duration_seconds": float(dur),
            "mp3_path": mp3_path,
        })
    rows.sort(key=lambda r: r["start_seconds"])
    return rows


# ---------------------------------------------------------------------------
# 2. Parse the translated script into ordered (label, text) blocks
#    (same label convention as script_v2_1.txt: a bare "section..." line)
# ---------------------------------------------------------------------------
LABEL_RE = re.compile(r"^section\S*$")


def parse_translated_sections(path: Path):
    blocks = []
    current_label = None
    current_lines = []

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            continue  # comment lines (e.g. the file's own header notes)
        if LABEL_RE.match(line):
            if current_label is not None:
                blocks.append((current_label, current_lines))
            current_label = line
            current_lines = []
        elif current_label is not None and line:
            current_lines.append(line)
    if current_label is not None:
        blocks.append((current_label, current_lines))

    return [(label, " ".join(lines)) for label, lines in blocks]


# ---------------------------------------------------------------------------
# 3. Sentence splitting (same heuristic as create_srt_file.py) and
#    char-budget chunking (no timing at this stage -- pure text splitting)
# ---------------------------------------------------------------------------
SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-ZÅÄÖ"])')


def split_sentences(text: str) -> list[str]:
    parts = SENTENCE_SPLIT_RE.split(text.strip())
    return [p for p in parts if p]


def chunk_text_by_chars(text: str, max_chars: int) -> list[str]:
    words = text.split()
    chunks, current, current_len = [], [], 0
    for w in words:
        added_len = current_len + (1 if current else 0) + len(w)
        if current and added_len > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [w], len(w)
        else:
            current.append(w)
            current_len = added_len
    if current:
        chunks.append(" ".join(current))
    return chunks


# ---------------------------------------------------------------------------
# 4. Build cues for one section: chunk sentence-by-sentence (so no cue
#    spans a sentence boundary), then place all of that section's cues
#    within its real [start, start+duration] window, each cue's slice of
#    time proportional to its own character count.
# ---------------------------------------------------------------------------
def build_section_cues(text: str, start: float, duration: float, max_chars: int):
    sentences = split_sentences(text) or [text]
    pieces = []
    for sentence in sentences:
        pieces.extend(chunk_text_by_chars(sentence, max_chars))
    if not pieces:
        return []

    total_chars = sum(len(p) for p in pieces) or 1
    cues = []
    cursor = start
    for piece in pieces:
        piece_dur = duration * (len(piece) / total_chars)
        cues.append((cursor, cursor + piece_dur, piece))
        cursor += piece_dur

    # Absorb rounding drift so the last cue ends exactly at the section's
    # known real end time, not a hair short/long from float accumulation.
    if cues:
        last_start, _, last_text = cues[-1]
        cues[-1] = (last_start, start + duration, last_text)
    return cues


# ---------------------------------------------------------------------------
# 5. SRT formatting (same as create_srt_file.py)
# ---------------------------------------------------------------------------
def fmt_timestamp(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues, out_path: Path):
    lines = []
    for i, (start, end, text) in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{fmt_timestamp(start)} --> {fmt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    default_clip_table = Path(__file__).resolve().parent / "audio_files_and_timings.txt"

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clip-table", type=Path, default=default_clip_table,
                     help="Same clip-timing table create_srt_file.py uses.")
    ap.add_argument("--translated-script", required=True, type=Path,
                     help="Translated text, in script_v2_1.txt's block format.")
    ap.add_argument("--out-srt", required=True, type=Path,
                     help="Path to write the translated .srt to.")
    ap.add_argument("--max-chars", type=int, default=34,
                     help="Max chars per subtitle cue line. Default: 34.")
    ap.add_argument("--language", default="sv",
                     help="Language code, for the printed log only.")
    args = ap.parse_args()

    if not args.clip_table.exists():
        raise SystemExit(
            f"Clip table not found: {args.clip_table}\n"
            f"Pass --clip-table explicitly, or place "
            f"audio_files_and_timings.txt next to this script."
        )

    rows = parse_clip_table(args.clip_table)
    sections = parse_translated_sections(args.translated_script)
    print(f"Parsed {len(rows)} clip-table rows, {len(sections)} translated blocks "
          f"(language: {args.language}).")

    if len(rows) != len(sections):
        raise SystemExit(
            f"Row/section count mismatch: {len(rows)} clip-table rows vs "
            f"{len(sections)} translated blocks. Fix the inputs before continuing."
        )

    all_cues = []
    for idx, (row, (label, text)) in enumerate(zip(rows, sections)):
        cues = build_section_cues(
            text, row["start_seconds"], row["duration_seconds"], args.max_chars
        )
        print(f"[{idx+1}/{len(rows)}] {label}: {len(cues)} cue(s)")
        all_cues.extend(cues)

    all_cues.sort(key=lambda c: c[0])
    write_srt(all_cues, args.out_srt)

    print(f"\nWrote {len(all_cues)} cues to {args.out_srt}")
    print(f"\nOutput file: {args.out_srt.resolve()}")


if __name__ == "__main__":
    main()
