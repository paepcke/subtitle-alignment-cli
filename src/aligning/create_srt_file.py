#!/usr/bin/env python3
# **********************************************************
# @Author: Andreas Paepcke
# @Date:   2026-09-18 08:27:29
# @File:   /Users/paepcke/VSCodeWorkspaces/subtitle-alignment-cli/src/aligning/create_srt_file.py
# @Last Modified by:   Andreas Paepcke
# @Last Modified time: 2026-09-18 17:08:04
# **********************************************************

"""
Forced-alignment pipeline for the ThTrainer tutorial video subtitles.

INPUTS
  --clip-table    The printed table produced by extract_mp3_clips.py
                  (e.g. audio_files_and_timings.txt). Each row gives an
                  absolute timeline `start_seconds` and on-timeline
                  `duration_seconds` for one per-section .mp3. Optional:
                  defaults to `audio_files_and_timings.txt` in this
                  script's own directory.
  --audio-script  script_v2_1.txt (the text that was read aloud to
                  produce the narration). Its ordered section blocks
                  correspond 1:1, by POSITION (not by label -- labels are
                  known to be inconsistent: "session" vs "section",
                  non-sequential and sometimes duplicate numbering), to
                  the clip-table rows.
  --max-chars     Maximum characters per subtitle cue line, used when
                  re-chunking the word-level alignment output into
                  subtitle-sized pieces. Default: 32.
  --language      Language code for WhisperX's alignment model (not the
                  narration's spoken language necessarily -- the model
                  that best matches the actual audio). Default: "en".

OUTPUTS
  --out-srt          A single English .srt for the whole video. Each
                      section is force-aligned independently (WhisperX
                      align()), so no section's alignment can drift into
                      a neighboring section's audio. Resulting word
                      timestamps are then offset by that section's
                      `start_seconds` to place them correctly in the
                      master timeline, and re-chunked into subtitle-sized
                      cues (see --max-chars).
  --out-boundaries    A JSON sidecar: the absolute [start, end] time of
                      each section in the final video, keyed by position
                      index and the script's raw label. These are the
                      reusable anchor points for the later dubbing phase
                      (where translated audio of a different duration
                      will need to be dropped into each section's slot).
                      Optional: defaults to `text_timing_<language>.json`
                      in the current directory (e.g. `text_timing_en.json`
                      for the default --language); if that name already
                      exists, a `_1`, `_2`, ... suffix is added rather
                      than overwriting.

USAGE
    python3 create_srt_file.py \
        --audio-script script_v2_1.txt \
        --out-srt en.srt \
        --max-chars 32
    # (--clip-table and --out-boundaries take their defaults above)
"""

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. Parse the clip table (as printed by extract_mp3_clips.py)
# ---------------------------------------------------------------------------
@dataclass
class ClipRow:
    start_seconds: float
    duration_seconds: float
    mp3_path: Path


ROW_RE = re.compile(
    r"^\s*(\d+):(\d+\.\d+)\s*\|\s*([\d.]+)s\s*\|\s*\S+\s*\|\s*(.+?)\s*$"
)


def parse_clip_table(path: Path) -> list[ClipRow]:
    rows = []
    for line in path.read_text().splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        mins, secs, dur, mp3_path = m.groups()
        rows.append(
            ClipRow(
                start_seconds=int(mins) * 60 + float(secs),
                duration_seconds=float(dur),
                mp3_path=Path(mp3_path),
            )
        )
    rows.sort(key=lambda r: r.start_seconds)
    return rows


# ---------------------------------------------------------------------------
# 2. Parse script_v2_1.txt into ordered (label, text) blocks
# ---------------------------------------------------------------------------
LABEL_RE = re.compile(r"^section\S*$")  # a bare label line, no spaces


def parse_script_sections(path: Path) -> list[tuple[str, str]]:
    blocks: list[tuple[str, list[str]]] = []
    current_label = None
    current_lines: list[str] = []

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
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
# 3. Sanity check: clip table rows vs script blocks must line up 1:1
# ---------------------------------------------------------------------------
def zip_rows_and_sections(rows, sections):
    if len(rows) != len(sections):
        raise SystemExit(
            f"Row/section count mismatch: {len(rows)} clip-table rows vs "
            f"{len(sections)} script blocks. Fix the inputs before aligning."
        )
    return list(zip(rows, sections))


# ---------------------------------------------------------------------------
# 4. Optional informational check: table duration vs real mp3 duration.
#    A mismatch is EXPECTED for any clip you intentionally trimmed on the
#    timeline (e.g. section15a) -- this just reports it, doesn't error.
# ---------------------------------------------------------------------------
def report_duration_diffs(rows: list[ClipRow]):
    for r in rows:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(r.mp3_path)],
                capture_output=True, text=True, check=True,
            )
            real_dur = float(out.stdout.strip())
        except Exception as e:
            print(f"  (could not ffprobe {r.mp3_path.name}: {e})")
            continue
        diff = real_dur - r.duration_seconds
        if abs(diff) > 0.05:
            print(f"  note: {r.mp3_path.name} on-timeline={r.duration_seconds:.2f}s "
                  f"vs source={real_dur:.2f}s (trimmed by {diff:.2f}s)")


# ---------------------------------------------------------------------------
# 4a. Split section text into sentence-level seed segments before alignment.
#
#    BUG THIS WORKS AROUND: whisperx.align() expects roughly one sentence
#    per input segment -- that's the shape WhisperX's own ASR step produces
#    (nltk sent_tokenize, "segment-per-sentence", explicitly for subtitling).
#    Handing it one segment containing an ENTIRE multi-sentence section (as
#    this script previously did) silently aligns only the first sentence
#    and drops everything after it -- no error, no warning, just missing
#    words/cues for every sentence past the first. Splitting into
#    per-sentence segments here, each with a proportional (word-count
#    weighted) time estimate to seed it, avoids that: align() still does
#    the real per-word timing against the actual audio, this just gives it
#    input in the shape it expects.
# ---------------------------------------------------------------------------
SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"])')


def split_sentences(text: str) -> list[str]:
    parts = SENTENCE_SPLIT_RE.split(text.strip())
    return [p for p in parts if p]


def build_sentence_segments(text: str, duration: float) -> list[dict]:
    sentences = split_sentences(text)
    if not sentences:
        return [{"text": text, "start": 0.0, "end": duration}]
    # Seed each sentence's rough start/end proportional to its word count --
    # a coarse guess only; align() refines actual per-word timing from here.
    word_counts = [len(s.split()) for s in sentences]
    total_words = sum(word_counts) or 1
    segments = []
    cursor = 0.0
    for sentence, wc in zip(sentences, word_counts):
        seg_dur = duration * (wc / total_words)
        segments.append({"text": sentence, "start": cursor, "end": cursor + seg_dur})
        cursor += seg_dur
    segments[-1]["end"] = duration  # absorb any rounding drift at the tail
    return segments


# ---------------------------------------------------------------------------
# 5. Forced alignment per section (WhisperX align(), no ASR/transcription)
# ---------------------------------------------------------------------------
def align_section(mp3_path: Path, text: str, duration: float, align_model,
                   align_metadata, device: str):
    import whisperx

    audio = whisperx.load_audio(str(mp3_path))
    segments = build_sentence_segments(text, duration)
    result = whisperx.align(
        segments, align_model, align_metadata, audio, device,
        return_char_alignments=False,
    )
    words = []
    for seg in result["segments"]:
        words.extend(seg["words"])
    # Drop any word WhisperX couldn't confidently place (rare, usually at
    # clip edges); keep everything else.
    return [w for w in words if "start" in w and "end" in w]


# ---------------------------------------------------------------------------
# 6. Re-chunk word timestamps into subtitle-sized cues
# ---------------------------------------------------------------------------
def chunk_words(words, offset: float, max_chars: int):
    cues = []
    current, current_len = [], 0
    for w in words:
        token = w["word"]
        added_len = current_len + (1 if current else 0) + len(token)
        if current and added_len > max_chars:
            cues.append(current)
            current, current_len = [w], len(token)
        else:
            current.append(w)
            current_len = added_len
    if current:
        cues.append(current)

    out = []
    for cue_words in cues:
        text = " ".join(w["word"] for w in cue_words)
        start = offset + cue_words[0]["start"]
        end = offset + cue_words[-1]["end"]
        out.append((start, end, text))
    return out


# ---------------------------------------------------------------------------
# 7. SRT formatting
# ---------------------------------------------------------------------------
def fmt_timestamp(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def next_available_path(path: Path) -> Path:
    """If `path` already exists, return the first `<stem>_1<suffix>`,
    `<stem>_2<suffix>`, ... that doesn't. Otherwise return `path` as-is."""
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


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

    ap = argparse.ArgumentParser()
    ap.add_argument("--clip-table", type=Path, default=default_clip_table,
                    help='List of audio clip start and duration times, plus their file paths.')
    ap.add_argument("--audio-script", required=True, type=Path,
                    help='Audio transcript text file.')
    ap.add_argument("--out-srt", required=True, type=Path,
                    help='Full path to the .srt output file')
    ap.add_argument("--out-boundaries", type=Path, default=None,
                    help="Destination of file with audio section start times in video. "
                         "Default: text_timing_<language>.json in the current directory.")
    ap.add_argument("--max-chars", type=int, default=32,
                    help="Max chars in a single subtitle.")
    ap.add_argument("--language", default="en",
                    help='Two-letter language code.')
    args = ap.parse_args()

    if not args.clip_table.exists():
        raise SystemExit(
            f"Clip table not found: {args.clip_table}\n"
            f"Pass --clip-table explicitly, or place "
            f"audio_files_and_timings.txt next to this script."
        )
    if args.out_boundaries is None:
        args.out_boundaries = Path(f"text_timing_{args.language}.json")
    args.out_boundaries = next_available_path(args.out_boundaries)

    rows = parse_clip_table(args.clip_table)
    sections = parse_script_sections(args.audio_script)
    print(f"Parsed {len(rows)} clip-table rows, {len(sections)} script blocks.")
    pairs = zip_rows_and_sections(rows, sections)

    print("Checking on-timeline durations against source mp3 files...")
    report_duration_diffs(rows)

    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading alignment model on {device}...")
    align_model, align_metadata = whisperx.load_align_model(
        language_code=args.language, device=device
    )

    all_cues = []
    boundaries = []
    for idx, (row, (label, text)) in enumerate(pairs):
        print(f"[{idx+1}/{len(pairs)}] Aligning {label} ({row.mp3_path.name})...")
        words = align_section(
            row.mp3_path, text, row.duration_seconds,
            align_model, align_metadata, device,
        )
        if not words:
            print(f"  warning: no words aligned for {label} -- skipping cues, "
                  f"still recording section boundary")
        else:
            all_cues.extend(chunk_words(words, row.start_seconds, args.max_chars))

        boundaries.append({
            "index": idx,
            "label": label,
            "start": row.start_seconds,
            "end": row.start_seconds + row.duration_seconds,
            "mp3_path": str(row.mp3_path),
        })

    all_cues.sort(key=lambda c: c[0])
    write_srt(all_cues, args.out_srt)
    args.out_boundaries.write_text(json.dumps(boundaries, indent=2))

    print(f"\nWrote {len(all_cues)} cues to {args.out_srt}")
    print(f"Wrote {len(boundaries)} section boundaries to {args.out_boundaries}")
    print("\nOutput files:")
    print(f"  SRT:        {args.out_srt.resolve()}")
    print(f"  Boundaries: {args.out_boundaries.resolve()}")


if __name__ == "__main__":
    main()
