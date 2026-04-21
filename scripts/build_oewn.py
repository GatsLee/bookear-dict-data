#!/usr/bin/env python3
"""
Convert Open English WordNet 2025 GWA XML into SQLite.

Source: https://en-word.net/static/english-wordnet-2025.xml.gz (CC BY 4.0)

Output tables:
  lemma(lemma PRIMARY KEY, phonetic, source_mask)
  sense_en(id PRIMARY KEY, lemma, pos, gloss, example)

Usage:
  python3 build_oewn.py ../data/oewn-2025.xml ../build/oewn.sqlite
"""

import sys
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

POS_MAP = {
    "n": "noun",
    "v": "verb",
    "a": "adjective",
    "s": "adjective",  # adjective satellite
    "r": "adverb",
    "c": "conjunction",
    "p": "adposition",
    "x": "other",
    "u": "unknown",
}

SOURCE_BIT_OEWN = 0b01


def build(xml_path: Path, db_path: Path) -> None:
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
        CREATE TABLE sense_en (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lemma TEXT NOT NULL,
            pos TEXT,
            gloss TEXT NOT NULL,
            example TEXT
        );
        CREATE INDEX idx_sense_en_lemma ON sense_en(lemma);
    """)

    print(f"[oewn] parsing {xml_path} ...")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Pass 1: build synset_id -> (pos, definition, examples)
    synsets = {}
    for synset in root.iter("Synset"):
        sid = synset.get("id")
        pos = POS_MAP.get(synset.get("partOfSpeech", ""), synset.get("partOfSpeech", ""))
        definition = ""
        examples = []
        for child in synset:
            if child.tag == "Definition" and child.text:
                if not definition:
                    definition = child.text.strip()
            elif child.tag == "Example" and child.text:
                examples.append(child.text.strip())
        if definition:
            synsets[sid] = (pos, definition, examples)
    print(f"[oewn] {len(synsets):,} synsets with definitions")

    # Pass 2: iterate LexicalEntry → for each Sense → resolve synset → emit sense_en row
    lemmas = {}  # lemma -> True
    rows_en = []
    entry_count = 0

    for entry in root.iter("LexicalEntry"):
        entry_count += 1
        lemma_el = entry.find("Lemma")
        if lemma_el is None:
            continue
        lemma = lemma_el.get("writtenForm", "").strip()
        if not lemma:
            continue
        lemma_lc = lemma.lower()
        lemmas[lemma_lc] = True

        for sense in entry.findall("Sense"):
            synset_id = sense.get("synset")
            if synset_id not in synsets:
                continue
            pos, gloss, examples = synsets[synset_id]
            example = examples[0] if examples else None
            rows_en.append((lemma_lc, pos, gloss, example))

    print(f"[oewn] {entry_count:,} entries, {len(rows_en):,} senses, {len(lemmas):,} unique lemmas")

    cur.executemany(
        "INSERT OR IGNORE INTO lemma(lemma, source_mask) VALUES(?, ?)",
        [(lem, SOURCE_BIT_OEWN) for lem in lemmas.keys()],
    )
    cur.executemany(
        "INSERT INTO sense_en(lemma, pos, gloss, example) VALUES(?, ?, ?, ?)",
        rows_en,
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM lemma")
    print(f"[oewn] lemma rows: {cur.fetchone()[0]:,}")
    cur.execute("SELECT COUNT(*) FROM sense_en")
    print(f"[oewn] sense_en rows: {cur.fetchone()[0]:,}")

    conn.close()
    print(f"[oewn] wrote {db_path} ({db_path.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: build_oewn.py <oewn.xml> <out.sqlite>", file=sys.stderr)
        sys.exit(2)
    build(Path(sys.argv[1]), Path(sys.argv[2]))
