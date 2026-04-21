#!/usr/bin/env python3
"""
Convert KRDict (한국어기초사전, 국립국어원) XML dumps into an English-keyed
SQLite index. KRDict is native Korean → foreign-language. We reverse it so
Bookear can look up an English lemma and get Korean definitions back.

Source: https://github.com/spellcheck-ko/korean-dict-nikl-krdict  (CC BY-SA 2.0 KR)
Audio fields are intentionally excluded (NIKL forbids redistribution).

Output tables:
  lemma(lemma PRIMARY KEY, phonetic, source_mask)   -- merged with OEWN later
  sense_kr(id, lemma, pos, gloss_ko, example_en, example_ko, krdict_headword)

Usage:
  python3 build_krdict.py <dir_of_xmls> <out.sqlite>
"""

import sys
import re
import sqlite3
from pathlib import Path

try:
    from lxml import etree as ET
    _HAS_LXML = True
except ImportError:
    import xml.etree.ElementTree as ET
    _HAS_LXML = False

SOURCE_BIT_KRDICT = 0b10

# Korean POS → English-friendly token
POS_KR_EN = {
    "명사": "noun",
    "대명사": "pronoun",
    "수사": "numeral",
    "동사": "verb",
    "형용사": "adjective",
    "관형사": "determiner",
    "부사": "adverb",
    "감탄사": "interjection",
    "조사": "particle",
    "어미": "ending",
    "접사": "affix",
}

# Drop obvious noise from English Equivalent lemmas
SPLIT_RE = re.compile(r"\s*[;,/]\s*|\s+or\s+")
STOPWORD_SUFFIX_RE = re.compile(r"\([^)]*\)")  # e.g. "edge (of a cliff)" → "edge"


def feat(element, attr_name):
    """Find <feat att="attr_name" val="..."/> child and return its val."""
    for child in element:
        if child.tag == "feat" and child.get("att") == attr_name:
            return child.get("val")
    return None


def normalize_english(raw: str) -> list[str]:
    """KRDict English equivalents can be 'edge; verge' or 'to strike (down)'.
    Split on separators, strip parentheticals, normalize lowercase."""
    out = []
    for chunk in SPLIT_RE.split(raw):
        cleaned = STOPWORD_SUFFIX_RE.sub("", chunk).strip()
        # Strip leading "to " from verb infinitives
        if cleaned.lower().startswith("to "):
            cleaned = cleaned[3:].strip()
        cleaned = cleaned.lower()
        if cleaned and all(c.isalpha() or c in " -'" for c in cleaned):
            out.append(cleaned)
    # De-dupe preserving order
    seen = set()
    result = []
    for w in out:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result


def build(xml_dir: Path, db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE lemma (
            lemma TEXT PRIMARY KEY,
            phonetic TEXT,
            source_mask INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE sense_kr (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lemma TEXT NOT NULL,
            pos TEXT,
            gloss_ko TEXT NOT NULL,
            example_en TEXT,
            example_ko TEXT,
            krdict_headword TEXT NOT NULL
        );
        CREATE INDEX idx_sense_kr_lemma ON sense_kr(lemma);
    """)

    xml_files = sorted(xml_dir.glob("*.xml"))
    if not xml_files:
        print(f"[krdict] no XML files in {xml_dir}", file=sys.stderr)
        sys.exit(1)

    rows_kr = []
    lemma_set = set()
    entry_count = 0
    sense_count = 0
    mapped_count = 0

    for xml_file in xml_files:
        print(f"[krdict] parsing {xml_file.name} ...")
        if _HAS_LXML:
            parser = ET.XMLParser(recover=True, encoding="utf-8")
            tree = ET.parse(str(xml_file), parser)
        else:
            tree = ET.parse(xml_file)
        lexicon = tree.getroot().find("Lexicon")
        if lexicon is None:
            continue

        for entry in lexicon.findall("LexicalEntry"):
            entry_count += 1
            lemma_el = entry.find("Lemma")
            if lemma_el is None:
                continue
            ko_headword = feat(lemma_el, "writtenForm")
            if not ko_headword:
                continue
            ko_pos = feat(entry, "partOfSpeech") or ""
            en_pos = POS_KR_EN.get(ko_pos, "")

            for sense in entry.findall("Sense"):
                sense_count += 1
                ko_def = feat(sense, "definition") or ""
                if not ko_def:
                    continue

                # English Equivalent
                en_equiv = None
                en_def = None
                for eq in sense.findall("Equivalent"):
                    if feat(eq, "language") == "영어":
                        en_equiv = feat(eq, "lemma")
                        en_def = feat(eq, "definition")
                        break
                if not en_equiv:
                    continue

                # Pick first example (prefer Korean sentence with English translation
                # if available; KRDict examples don't contain inline translations,
                # so we use the Korean example as both).
                example_ko = None
                for ex in sense.findall("SenseExample"):
                    example_ko = feat(ex, "example")
                    if example_ko:
                        break

                for en_lemma in normalize_english(en_equiv):
                    lemma_set.add(en_lemma)
                    rows_kr.append((
                        en_lemma,
                        en_pos,
                        ko_def,
                        None,  # example_en — KRDict doesn't carry English examples
                        example_ko,
                        ko_headword,
                    ))
                    mapped_count += 1

    print(
        f"[krdict] entries={entry_count:,} senses={sense_count:,} "
        f"en-mapped={mapped_count:,} unique-en-lemmas={len(lemma_set):,}"
    )

    cur.executemany(
        "INSERT OR IGNORE INTO lemma(lemma, source_mask) VALUES(?, ?)",
        [(lem, SOURCE_BIT_KRDICT) for lem in lemma_set],
    )
    cur.executemany(
        "INSERT INTO sense_kr(lemma, pos, gloss_ko, example_en, example_ko, krdict_headword) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        rows_kr,
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM sense_kr")
    print(f"[krdict] sense_kr rows: {cur.fetchone()[0]:,}")

    conn.close()
    print(f"[krdict] wrote {db_path} ({db_path.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: build_krdict.py <xml_dir> <out.sqlite>", file=sys.stderr)
        sys.exit(2)
    build(Path(sys.argv[1]), Path(sys.argv[2]))
