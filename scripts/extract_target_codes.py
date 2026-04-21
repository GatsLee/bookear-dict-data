#!/usr/bin/env python3
"""
Scan the locally-cloned KRDict mirror (data/krdict/*.xml) for all
<LexicalEntry att="id" val="..."/> values and emit them as a sorted unique
list. Used as input to fetch_krdict_api.py for a complete, fresh API pull.

Usage:
  python3 extract_target_codes.py ../data/krdict ../data/target_codes.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from lxml import etree as ET
except ImportError:
    import xml.etree.ElementTree as ET


def feat(element, attr):
    for child in element:
        if child.tag == "feat" and child.get("att") == attr:
            return child.get("val")
    return None


def has_english_equivalent(entry) -> bool:
    """True if this LexicalEntry has at least one <Equivalent language='영어'/>
    inside any of its senses."""
    for eq in entry.findall(".//Equivalent"):
        for child in eq:
            if child.tag == "feat" and child.get("att") == "language" and child.get("val") == "영어":
                return True
    return False


def main(xml_dir: Path, out_path: Path, english_only: bool) -> None:
    codes: set[int] = set()
    kept = 0
    skipped = 0
    for xml_file in sorted(xml_dir.glob("*.xml")):
        print(f"[extract] {xml_file.name}")
        try:
            parser = ET.XMLParser(recover=True, encoding="utf-8")
            tree = ET.parse(str(xml_file), parser)
        except Exception as e:
            print(f"[extract] skipping {xml_file.name}: {e}", file=sys.stderr)
            continue
        root = tree.getroot()
        lexicon = root.find("Lexicon") if root is not None else None
        if lexicon is None:
            continue
        for entry in lexicon.findall("LexicalEntry"):
            val = entry.get("val")
            if not val or not val.isdigit():
                continue
            if english_only and not has_english_equivalent(entry):
                skipped += 1
                continue
            codes.add(int(val))
            kept += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for c in sorted(codes):
            f.write(f"{c}\n")
    mode = "english-only" if english_only else "all"
    print(f"[extract] wrote {len(codes):,} target_codes ({mode}) to {out_path}")
    if english_only:
        print(f"[extract] kept={kept:,} skipped_no_english={skipped:,}")


if __name__ == "__main__":
    args = sys.argv[1:]
    english_only = False
    if "--english-only" in args:
        english_only = True
        args.remove("--english-only")
    if len(args) != 2:
        print("usage: extract_target_codes.py [--english-only] <xml_dir> <out.txt>",
              file=sys.stderr)
        sys.exit(2)
    main(Path(args[0]), Path(args[1]), english_only)
