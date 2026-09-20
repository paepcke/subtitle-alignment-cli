#!/usr/bin/env python3
# **********************************************************
#
# @Author: Andreas Paepcke
# @Date:   2026-09-17 13:00:52
# @File:   /Users/paepcke/VSCodeWorkspaces/subtitle-alignment-cli/src/screen_flow_extraction/extract_mp3_clips.py
# @Last Modified by:   Andreas Paepcke
# @Last Modified time: 2026-09-17 13:41:10
#
# **********************************************************
#!/usr/bin/env python3
"""
Extract audio (.mp3) clip file paths and their timeline start times from a
ScreenFlow ScreenFlowDocument.dat file.

BACKGROUND (why this isn't a simple plist read):
  ScreenFlowDocument.dat is a Core Data "Binary Store" (an NSKeyedArchiver-
  serialized object graph, wrapped in a small custom "CoreData" header).
  Each row is generic: an entity name, a primary key, an ORDERED array of
  attribute values (no field names attached), and a dict of relationships
  (which ARE named). The attribute order is: all persisted (non-transient)
  attributes of the entity and its superentities, sorted alphabetically by
  name. To know which array index is "startTime" vs "duration" etc. we
  read that same ordering out of ScreenFlow's own compiled Core Data model
  (a .mom file inside ScreenFlow.app), which is itself just another
  NSKeyedArchiver plist.

REQUIREMENTS:
  - No PyPI packages needed (plistlib is in the standard library).
  - You need ScreenFlow's compiled Core Data model file (.mom) that
    matches the version used to save this specific document. Find it
    with, in Terminal:
        find /Applications/ScreenFlow*.app -iname "*.mom*"
    This script will automatically pick the *.mom file whose checksum
    matches the one recorded in your .dat file's metadata (via each
    momd folder's VersionInfo.plist), so you can just point MOMD_DIR at
    the whole "Scarlett.momd" (or similarly named) folder found above
    and not worry about which individual .mom is "the right one".

USAGE:
    python3 extract_mp3_clips.py
"""

import plistlib
import struct
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIGURATION -- adjust MOMD_DIR if ScreenFlow's model folder is elsewhere.
# ---------------------------------------------------------------------------
script_dir = Path(__file__).resolve().parent
DAT_PATH = script_dir / "ScreenFlowDocument.dat"
MOMD_DIR = Path("/Applications/ScreenFlow.app/Contents/Resources/Scarlett.momd")


# ---------------------------------------------------------------------------
# Generic NSKeyedArchiver resolver: turns plistlib's raw UID-reference graph
# into plain nested Python dicts/lists. Dicts get an added "__class__" key
# (the archived Cocoa class name). NSDictionary/NSArray-pattern objects get
# "__dict__"/"__array__" keys holding the actual resolved contents.
# ---------------------------------------------------------------------------
def load_archive(raw: bytes, start: int, end: int):
    data = plistlib.loads(raw[start:end])
    objects = data["$objects"]
    memo = {}

    def resolve(obj):
        if isinstance(obj, plistlib.UID):
            idx = int(obj)
            if idx in memo:
                return memo[idx]
            raw_val = objects[idx]
            if raw_val == "$null":
                memo[idx] = None
                return None
            if isinstance(raw_val, dict):
                cls_name = None
                if "$class" in raw_val:
                    cls_idx = int(raw_val["$class"])
                    cls_obj = objects[cls_idx]
                    cls_name = cls_obj.get("$classname") if isinstance(cls_obj, dict) else None
                if "NS.keys" in raw_val and "NS.objects" in raw_val:
                    placeholder = {"__class__": cls_name, "__dict__": {}}
                    memo[idx] = placeholder
                    keys = [resolve(k) for k in raw_val["NS.keys"]]
                    vals = [resolve(v) for v in raw_val["NS.objects"]]
                    placeholder["__dict__"] = dict(zip(keys, vals))
                    return placeholder
                if "NS.objects" in raw_val:
                    placeholder = {"__class__": cls_name, "__array__": []}
                    memo[idx] = placeholder
                    placeholder["__array__"] = [resolve(v) for v in raw_val["NS.objects"]]
                    return placeholder
                if "NS.data" in raw_val:
                    d = raw_val["NS.data"]
                    memo[idx] = {
                        "__class__": cls_name,
                        "__data_len__": len(d) if isinstance(d, (bytes, bytearray)) else None,
                        "__data__": d,
                    }
                    return memo[idx]
                if "NS.string" in raw_val or "NS.mstring" in raw_val:
                    s = raw_val.get("NS.string", raw_val.get("NS.mstring"))
                    memo[idx] = s
                    return s
                placeholder = {"__class__": cls_name}
                memo[idx] = placeholder
                for k, v in raw_val.items():
                    if k == "$class":
                        continue
                    placeholder[k] = resolve(v)
                return placeholder
            elif isinstance(raw_val, list):
                placeholder = []
                memo[idx] = placeholder
                for item in raw_val:
                    placeholder.append(resolve(item))
                return placeholder
            else:
                memo[idx] = raw_val
                return raw_val
        elif isinstance(obj, dict):
            return {k: resolve(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [resolve(v) for v in obj]
        else:
            return obj

    top_key = list(data["$top"].keys())[0]
    return resolve(data["$top"][top_key])


# ---------------------------------------------------------------------------
# Locate the correct .mom model file by matching the checksum recorded in
# the document's own metadata section against each candidate .mom's
# checksum (from the momd folder's VersionInfo.plist).
# ---------------------------------------------------------------------------
def find_header_sections(raw: bytes):
    """Parse the small custom 'CoreData' header to find the (offset, length)
    of the main data section and the metadata section, rather than relying
    on hardcoded offsets."""
    assert raw[:8] == b"CoreData", "Not a Core Data binary atomic store file"
    body = raw[8:64]

    def u64(off):
        return struct.unpack(">Q", body[off:off + 8])[0]

    # Empirically: three (offset, length) pairs follow the version/flags
    # words. The metadata section is the smaller one; the main data
    # section is the larger one starting right after the 64-byte header.
    metadata_offset = u64(8)
    metadata_len = u64(16)
    main_offset = u64(24)
    main_len = u64(32)
    return (main_offset, main_offset + main_len), (metadata_offset, metadata_offset + metadata_len)


def get_document_model_checksum(raw: bytes, metadata_span):
    meta_root = load_archive(raw, *metadata_span)
    d = meta_root.get("__dict__", meta_root)
    return d.get("NSStoreModelVersionChecksumKey")


def find_matching_mom(momd_dir: Path, checksum: str) -> Path:
    version_info_path = momd_dir / "VersionInfo.plist"
    with open(version_info_path, "rb") as f:
        version_info = plistlib.load(f)
    checksums = version_info["NSManagedObjectModel_VersionChecksums"]
    for mom_name, cksum in checksums.items():
        if cksum == checksum:
            candidate = momd_dir / f"{mom_name}.mom"
            if candidate.exists():
                return candidate
    # Fall back to the folder's declared "current" version.
    current = version_info.get("NSManagedObjectModel_CurrentVersionName")
    if current:
        candidate = momd_dir / f"{current}.mom"
        if candidate.exists():
            print(f"  (warning: no exact checksum match; falling back to "
                  f"current version '{current}')")
            return candidate
    raise FileNotFoundError(
        f"Could not find a .mom file in {momd_dir} matching this document's model checksum."
    )


# ---------------------------------------------------------------------------
# Build, from the .mom model, a per-entity ordered list of persisted
# (non-transient) attribute names -- including those inherited from
# superentities -- matching Core Data Binary Store's actual serialization
# order (alphabetical by name).
# ---------------------------------------------------------------------------
class Model:
    def __init__(self, mom_path: Path):
        raw = mom_path.read_bytes()
        root = load_archive(raw, 0, len(raw))
        self.entities = root["NSEntities"]["__dict__"]
        self._attr_cache = {}

    def _own_persisted_attrs(self, entity_name):
        ent = self.entities[entity_name]
        props = ent["NSProperties"]["__dict__"]
        return [
            name for name, desc in props.items()
            if desc.get("__class__") == "NSAttributeDescription"
            and desc.get("NSIsTransient") is not True
        ]

    def persisted_attrs_ordered(self, entity_name):
        """All persisted attribute names for this entity AND its
        superentities, sorted alphabetically -- this is the order Core
        Data's Binary Store uses for the NSAttributeValues array."""
        if entity_name in self._attr_cache:
            return self._attr_cache[entity_name]
        names = set(self._own_persisted_attrs(entity_name))
        ent = self.entities[entity_name]
        sup = ent.get("NSSuperentity")
        while isinstance(sup, dict):
            sup_name = sup.get("NSEntityName")
            if sup_name:
                names.update(self._own_persisted_attrs(sup_name))
            sup = sup.get("NSSuperentity")
        ordered = sorted(names)
        self._attr_cache[entity_name] = ordered
        return ordered


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------
def main():
    raw = DAT_PATH.read_bytes()
    main_span, metadata_span = find_header_sections(raw)

    checksum = get_document_model_checksum(raw, metadata_span)
    print(f"Document model checksum: {checksum}")

    mom_path = find_matching_mom(MOMD_DIR, checksum)
    print(f"Using model file: {mom_path.name}")

    model = Model(mom_path)

    # Parse the main object graph.
    root = load_archive(raw, *main_span)
    rows = root["__dict__"]  # primary_key -> row dict

    # Find DocumentProperties row to get the project's tick timescale.
    doc_props_row = None
    for row in rows.values():
        if row.get("NSEntityName") == "DocumentProperties":
            doc_props_row = row
            break
    if doc_props_row is None:
        raise RuntimeError("Could not find a DocumentProperties row -- unexpected document structure.")

    dp_names = model.persisted_attrs_ordered("DocumentProperties")
    dp_values = doc_props_row["NSAttributeValues"]["__array__"]
    dp_fields = dict(zip(dp_names, dp_values))
    timescale = dp_fields.get("timeScale")
    if not timescale:
        raise RuntimeError("Could not determine document timeScale.")
    print(f"Document tick timescale: {timescale} ticks/second\n")

    # Build a quick primary-key -> row lookup (NSPrimaryKey64 -> row).
    by_pk = {row.get("NSPrimaryKey64"): row for row in rows.values() if isinstance(row, dict)}

    results = []
    for row in rows.values():
        if not isinstance(row, dict):
            continue
        entity_name = row.get("NSEntityName")
        if not entity_name:
            continue

        rel = row.get("NSRelatedNodes", {}).get("__dict__", {})
        source_rel = rel.get("source")
        if source_rel is None:
            continue
        source_pks = source_rel.get("__array__", []) if isinstance(source_rel, dict) else []
        if not source_pks:
            continue
        source_pk = source_pks[0]
        source_row = by_pk.get(source_pk)
        if source_row is None or source_row.get("NSEntityName") != "MediaSource":
            continue

        ms_names = model.persisted_attrs_ordered("MediaSource")
        ms_values = source_row["NSAttributeValues"]["__array__"]
        ms_fields = dict(zip(ms_names, ms_values))
        file_path = ms_fields.get("filePath")
        if not isinstance(file_path, str) or not file_path.lower().endswith(".mp3"):
            continue

        clip_names = model.persisted_attrs_ordered(entity_name)
        clip_values = row["NSAttributeValues"]["__array__"]
        clip_fields = dict(zip(clip_names, clip_values))

        start_raw = clip_fields.get("startTime")
        duration_raw = clip_fields.get("duration")
        if start_raw is None:
            continue

        start_seconds = start_raw / timescale
        duration_seconds = duration_raw / timescale if duration_raw is not None else None

        results.append({
            "mp3_path": file_path,
            "start_seconds": start_seconds,
            "duration_seconds": duration_seconds,
            "clip_entity": entity_name,
            "clip_pk": row.get("NSPrimaryKey64"),
        })

    results.sort(key=lambda r: r["start_seconds"])

    print(f"Found {len(results)} audio (.mp3) clip(s) on the timeline:\n")
    print(f"{'Start':>10} | {'Duration':>9} | {'Clip type':<16} | File")
    print("-" * 100)
    for r in results:
        s = r["start_seconds"]
        mins, secs = divmod(s, 60)
        start_str = f"{int(mins):02d}:{secs:05.2f}"
        dur_str = f"{r['duration_seconds']:.2f}s" if r["duration_seconds"] is not None else "?"
        print(f"{start_str:>10} | {dur_str:>9} | {r['clip_entity']:<16} | {r['mp3_path']}")


if __name__ == "__main__":
    main()
