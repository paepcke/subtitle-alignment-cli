# TherapistTrainer Tutorial Video — Subtitle Pipeline Procedure

End-to-end procedure: from the ScreenFlow project file to a tutorial webpage and
a standalone .mp4, both carrying toggleable English / Swedish / Spanish /
German subtitles.

Convention: `$PROJ_ROOT` = the tutorial video project folder, e.g.
`/Users/paepcke/Project/TherapistTrainer/VideoIntro/ThTrainerV2_0`.

## Repo layout

```
src
├── aligning
│   ├── __init__.py
│   ├── audio_files_and_timings.txt   ← output of Stage 1, input to Stages 2 & 3
│   ├── create_srt_file.py            ← Stage 2 (English, forced alignment)
│   ├── create_translated_srt.py      ← Stage 3 (translated languages)
│   └── create_vtt_from_srt.py        ← Stage 5 (browser captions)
├── attaching
│   ├── __init__.py
│   └── attach_srt_to_video.py        ← Stage 4
└── screen_flow_extraction
    ├── __init__.py
    ├── extract_mp3_clips.py          ← Stage 1
    └── ScreenFlowDocument.dat        ← copied out of the .screenflow project bundle
```

## Stage 1 — Extract clip timing from the ScreenFlow project

ScreenFlow's project file (`ScreenFlowDocument.dat`) is a Core Data "Binary Store"
(NSKeyedArchiver-serialized). Each clip row is a generic entity + an *ordered*
array of attribute values with no field names attached. `extract_mp3_clips.py`
reads the same attribute ordering out of ScreenFlow's own compiled Core Data
model (a `.mom` file inside ScreenFlow.app), matched to the document via a
checksum ScreenFlow itself records, so the right model version is found
automatically.

- No third-party packages needed (stdlib `plistlib` only).
- If ScreenFlow's model folder isn't at the default
  `/Applications/ScreenFlow.app/Contents/Resources/Scarlett.momd`, adjust
  `MOMD_DIR` at the top of the script — find it with
  `find /Applications/ScreenFlow*.app -iname "*.mom*"`.
- Copy the project's `ScreenFlowDocument.dat` next to the script before running.

```bash
cd src/screen_flow_extraction
python3 extract_mp3_clips.py > ../aligning/audio_files_and_timings.txt
```

Prints one row per on-timeline .mp3 clip (absolute start, on-timeline duration,
clip type, source path), sorted by start time — redirected straight into the
file Stages 2 and 3 default to.

## Stage 2 — Build the English subtitles (forced alignment)

`create_srt_file.py` knows the exact narration text per section
(`script_v2_1.txt`) and the exact real audio for it (Stage 1's clip table). It
uses WhisperX's forced-*alignment* step only (`whisperx.align()`) — not
transcription — to find exactly when each known word is spoken. Each section
aligns independently against its own .mp3 so timing can't drift across
sections; resulting word timestamps are offset into the master timeline.

Script and audio-script blocks are matched by **position**, not label text —
labels are known to be inconsistent ("session" vs "section", non-sequential
numbering).

**Bug this script works around:** WhisperX's `align()` expects ~one sentence
per input segment. Feeding it a whole multi-sentence section as one segment
silently aligns only the first sentence and drops the rest — no error, no
warning. Fix: split each section into per-sentence seed segments (word-count
proportional time estimate) before calling `align()`.

```bash
python3 src/aligning/create_srt_file.py \
    --audio-script $PROJ_ROOT/script_v2_1.txt \
    --out-srt $PROJ_ROOT/thtrainer_2_0_script_2_1.srt \
    --max-chars 40
```

Key options: `--clip-table` (default: `audio_files_and_timings.txt` next to
the script), `--audio-script` (required), `--max-chars` (default 32),
`--language` (WhisperX alignment-model code, default `en`), `--out-boundaries`
(default `text_timing_<language>.json`, auto-suffixed `_1`, `_2`, ... instead
of overwriting).

The boundaries JSON records each section's absolute [start, end] in the final
video — anchor points a future dubbing phase would need. It depends only on
the clip-timing table, never on subtitle language, so it's written **once**,
here in Stage 2 (see Stage 3 note).

## Stage 3 — Build translated subtitles

No foreign-language *audio* exists — the video's narration audio stays English
regardless of subtitle track. Two parts:

### 3a. Translate the script (human step)

Plain translation (not WhisperX, not ElevenLabs). Produce a text file in
**exactly the same block format** as `script_v2_1.txt`:

- Same `sectionN` label lines, verbatim, same order (positional zipping against
  the clip-timing table, same as Stage 2).
- Quoted UI element names (e.g. "Client History", "Send", "Session Critique")
  left in English — the on-screen app isn't localized.
- Recommend native-speaker clinician review before shipping, especially ISTDP
  terminology.
- Keep sentences reasonably contracted/tight. The narration audio is a fixed
  length per section, and Stage 3b spreads that fixed duration across
  whatever text is here — wordier phrasing means a higher reading speed
  (characters-per-second) for the viewer, not a longer video. This was
  revisited once already for the German and Swedish scripts specifically to
  bring the CPS down.

### 3b. Time the cues

`create_translated_srt.py` takes each section's real, known
[start, start+duration] window from the same clip-timing table (exact
boundaries). Within a section, with no foreign speech to align against, it
splits the translated text into subtitle-sized cues and distributes the
section's real duration across them **proportional to each cue's character
count** — a solid starting point, not a final cut; nudge by hand (e.g.
Aegisub) if a cue looks oddly paced.

```bash
python3 src/aligning/create_translated_srt.py \
    --translated-script $PROJ_ROOT/script_v2_1_swedish.txt \
    --out-srt $PROJ_ROOT/thtrainer_2_0_script_2_1_swedish.srt \
    --max-chars 40

python3 src/aligning/create_translated_srt.py \
    --translated-script $PROJ_ROOT/script_v2_1_spanish.txt \
    --out-srt $PROJ_ROOT/thtrainer_2_0_script_2_1_spanish.srt \
    --max-chars 40

python3 src/aligning/create_translated_srt.py \
    --translated-script $PROJ_ROOT/script_v2_1_german.txt \
    --out-srt $PROJ_ROOT/thtrainer_2_0_script_2_1_german.srt \
    --max-chars 40
```

**This script deliberately writes no boundaries JSON** — boundaries come from
`audio_files_and_timings.txt` alone, never from subtitle language/text, so
Stage 2 owning that file once is the single source of truth. A per-language
copy here would just duplicate it under a different name.

## Stage 4 — Attach every subtitle track to the video (for native players)

`attach_srt_to_video.py` layers one or more `.srt` files onto the .mp4 as
soft/toggleable subtitle tracks. Video/audio are stream-copied (`-c copy`);
only subtitle text is transcoded, into `mov_text` (the one subtitle codec MP4
containers support). This is what VLC, QuickTime, and OS-level mobile players
read directly from the file itself.

**Always attach every language in a single run, from the ORIGINAL unsubtitled
video** — never by pointing `--video` at a previously-subtitled output to
layer one more language on. Two real, silent ffmpeg bugs, confirmed by direct
testing:

1. **Daisy-chaining wipes track titles.** Attach English, re-run to add
   Swedish onto that output, re-run again to add Spanish onto that — by the
   third run, English's and Swedish's *titles* are both gone (players fall
   back to generic "Track N"), even though their *language* tags survive.
   Confirmed this happens after just **one** remux hop, not only the third.
   Language lives in the MP4 track header and survives a remux; title lives in
   a separate metadata atom ffmpeg's mov demuxer doesn't reliably re-expose on
   a second remux. Attaching every track in one invocation has only one
   generation, so the bug never triggers.
2. **A 2-letter language code fails silently.** MP4 requires the 3-letter ISO
   639-2 code (`spa`), not the 2-letter ISO 639-1 code (`es`). Wrong length
   doesn't error — ffmpeg just drops the language tag with no message. The
   script now validates for a 3-letter alphabetic code up front and refuses to
   run otherwise.

```bash
python3 src/attaching/attach_srt_to_video.py \
    --video $PROJ_ROOT/FinalProduct/therapistTrainerTutorial_2_1.mp4 \
    --srt $PROJ_ROOT/thtrainer_2_0_script_2_1_english.srt --language eng --label English \
    --srt $PROJ_ROOT/thtrainer_2_0_script_2_1_swedish.srt --language swe --label Swedish \
    --srt $PROJ_ROOT/thtrainer_2_0_script_2_1_spanish.srt --language spa --label Spanish \
    --srt $PROJ_ROOT/thtrainer_2_0_script_2_1_german.srt  --language deu --label Deutsch \
    --out $PROJ_ROOT/therapistTrainerTutorial_2_1_subtitled.mp4
```

`--srt`/`--language` repeat once per track, in matching order; `--label` is
optional but if given must be given once per track too. To add a language
later, re-run this full command (from the original video) with the new track
added — don't chain onto the already-subtitled output.

## Stage 5 — Convert every .srt to .vtt (for the browser tutorial page)

**Why this stage exists at all:** the tutorial webpage plays the video through
an HTML `<video>` element, and a browser's `<video>` element does **not** read
the `mov_text` subtitle tracks Stage 4 just embedded in the .mp4 — that only
works for native players (VLC, QuickTime, mobile OS players). The *only*
subtitle mechanism a web `<video>` element understands is an explicit
`<track>` child element pointing at a separate WebVTT (`.vtt`) file served
alongside the page. No `<track>`, no "CC" button in the browser's controls at
all — this was the root cause of subtitles not being "obvious" in Chrome; they
weren't hidden, the browser was never given them.

`create_vtt_from_srt.py` converts each existing `.srt` into a `.vtt` reusing
the same timing/text (SRT and WebVTT are near-identical: WebVTT needs a
literal `WEBVTT` header line and `.` instead of `,` in timestamps).

```bash
src/aligning/create_vtt_from_srt.py $PROJ_ROOT/thtrainer_2_0_script_2_1_english.srt  --line 90%
src/aligning/create_vtt_from_srt.py $PROJ_ROOT/thtrainer_2_0_script_2_1_swedish.srt --line 90%
src/aligning/create_vtt_from_srt.py $PROJ_ROOT/thtrainer_2_0_script_2_1_spanish.srt --line 90%
src/aligning/create_vtt_from_srt.py $PROJ_ROOT/thtrainer_2_0_script_2_1_german.srt --line 90%
```

Each `.vtt` is written next to its source `.srt` by default (same name, `.vtt`
extension).

**Why `--line 90%` and not the default:** left alone, every cue's vertical
position defaults to WebVTT's `line:auto`. In desktop Chrome, `auto` nudges
captions up whenever the native player control bar is visible (to avoid
overlapping it) and back down once it fades, and *also* pushes a cue up
further if its text wraps to 2 lines — which happened more for German, due to
longer compound words. Combined, that produced 2-3 different observed caption
heights for what should be one consistent position. Passing `--line 90%`
(the same value for every language, so all four sit at an identical height)
pins every cue to one fixed position instead, eliminating that jitter and
letting the caption sit low — into the black band at the bottom of the frame
on this video — rather than wherever Chrome's auto-avoidance happens to land
it.

Platform note: iOS/iPadOS Safari plays video through AVKit's own native
player and caption renderer (not the same code path as desktop Chrome), which
dynamically recalculates its caption safe-area when the control bar
shows/hides — so on an iPad the caption visibly drops further down once the
controls fade, even with a fixed `--line` value. Desktop Chrome computes the
`line:` position once and does not re-lay-out on control visibility changes.
Both are correct, expected behavior for that platform's renderer, not a bug
in the `.vtt` file.

Trade-off to keep in mind if `--line` is ever pushed higher than 90% (further
down): an explicit `line` value stops Chrome's automatic avoidance of the
control bar, so a value very close to 100% can let the native seek bar
overlap the caption while a viewer is hovering the video on desktop.

## Stage 6 — Deploy the subtitle files to the tutorial webpage

```bash
cp $PROJ_ROOT/*.vtt ~/VSCodeWorkspaces/therapist/front_end/html/tutorial/
```

The tutorial page's `index.html` (in that same `tutorial/` folder) already
declares one `<track kind="subtitles">` element per language pointing at
these four filenames, plus:
- `<meta charset="UTF-8">` in `<head>` — without it, non-ASCII characters in
  page text (e.g. the "ñ" in the Spanish track's `label="Español"`) get
  mis-decoded into mojibake by the browser's encoding guess.
- a `video::cue { font-size: ...; }` rule — the only way to control the size
  of browser-rendered `<track>` captions; ordinary CSS rules targeting the
  video or a class do not reach caption text at all, since the browser draws
  it into a separate caption layer outside the normal DOM.

**Remember:** copying the `.vtt` files into the front-end folder is a local
filesystem change only. The `therapist` git project still needs its changes
**committed and pushed**, and the **production server** still needs to be
updated with the new build, before viewers actually see the new subtitles.

## Gotchas reference

| Symptom | Cause | Fix |
|---|---|---|
| WhisperX .srt silently missing every sentence after a section's first | `align()` expects ~one sentence per segment; a whole multi-sentence section as one segment truncates silently | Split into per-sentence seed segments before `align()` (`create_srt_file.py`) |
| No way to time translated cues precisely within a section | No foreign-language audio to force-align against | Distribute real section duration across cues proportional to character count (`create_translated_srt.py`) |
| VLC subtitle menu shows generic "Track N" instead of the language name after adding one more language | Daisy-chained ffmpeg remuxing drops previously-set track TITLE metadata (language tag survives) | Attach every language in one single ffmpeg invocation, from the original unsubtitled video |
| A subtitle track ends up with no language tag at all, no error shown | 2-letter ISO 639-1 code ("es") given where MP4 requires the 3-letter ISO 639-2 code ("spa") | `attach_srt_to_video.py` validates for a 3-letter code up front and refuses to run otherwise |
| Multiple duplicate `text_timing_N.json` files accumulating | Boundaries only depend on the clip-timing table, never on subtitle language | Only `create_srt_file.py` (Stage 2) writes this file, once per project; `create_translated_srt.py` writes none |
| Chrome never shows a "CC" button at all, even though the .mp4 has subtitle tracks | A web `<video>` element does not read embedded `mov_text` tracks from the container — only an explicit `<track>` + `.vtt` file | Convert every `.srt` to `.vtt` (Stage 5) and reference it with a `<track>` element in `index.html` |
| Non-ASCII text in the page (e.g. "Español") renders as garbled characters | `index.html` has no declared character encoding, so the browser guesses wrong | Add `<meta charset="UTF-8">` as the first element in `<head>` |
| Caption text is much larger than it needs to be | Browser default size for `<track>` captions; ordinary CSS can't reach it | Style it with the `video::cue { font-size: ...; }` pseudo-element — the only selector that reaches caption text |
| Caption vertical position jumps between 2-3 different heights, worse for German | `line:auto` (WebVTT default) moves captions to avoid the control bar and pushes 2-line-wrapped cues up further | Bake a fixed `--line` value (e.g. `90%`) into every cue via `create_vtt_from_srt.py`, same value for every language |
