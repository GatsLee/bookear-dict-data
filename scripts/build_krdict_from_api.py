#!/usr/bin/env python3
"""
Convert the KRDict API dump (JSONL from fetch_krdict_api.py) into an
English-keyed SQLite matching the schema produced by build_krdict.py.

Key difference vs build_krdict.py: each English lemma only lands in sense_kr
**if it also appears in OEWN** (i.e., the English side is a recognized word).
This guarantees Bookear always has a paired English definition when showing
the Korean gloss, and drops junk English phrases that KRDict occasionally
emits ("to hit with great force", "a sort of small bird", etc.).

Usage:
  python3 build_krdict_from_api.py \
      data/krdict-api-dump.jsonl \
      build/oewn.sqlite \
      build/krdict-api.sqlite
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

SOURCE_BIT_KRDICT = 0b10

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

SPLIT_RE = re.compile(r"\s*[;,/]\s*|\s+or\s+")
PARENS_RE = re.compile(r"\([^)]*\)")


def normalize_english_phrase(raw: str) -> list[str]:
    """Clean up 'edge; verge' → ['edge', 'verge'].
    Also strips infinitive 'to ' and parentheticals, and keeps multi-word
    phrases intact (e.g. 'look down on' stays as one lemma)."""
    if not raw:
        return []
    out: list[str] = []
    for chunk in SPLIT_RE.split(raw):
        cleaned = PARENS_RE.sub("", chunk).strip()
        if cleaned.lower().startswith("to "):
            cleaned = cleaned[3:].strip()
        cleaned = cleaned.lower()
        if not cleaned:
            continue
        if all(c.isalpha() or c in " -'" for c in cleaned):
            out.append(cleaned)
    seen = set()
    result: list[str] = []
    for w in out:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result


def load_oewn_lemmas(oewn_sqlite: Path) -> set[str]:
    conn = sqlite3.connect(oewn_sqlite)
    cur = conn.cursor()
    cur.execute("SELECT lemma FROM lemma")
    lemmas = {row[0] for row in cur.fetchall()}
    conn.close()
    print(f"[build-kr-api] loaded {len(lemmas):,} OEWN lemmas")
    return lemmas


def build(dump_path: Path, oewn_path: Path, out_path: Path) -> None:
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    oewn_lemmas = load_oewn_lemmas(oewn_path)

    conn = sqlite3.connect(out_path)
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

    total_entries = 0
    total_senses = 0
    matched_senses = 0
    unmatched_english = 0
    lemma_set: set[str] = set()
    rows: list[tuple] = []

    with dump_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            total_entries += 1

            ko_headword = entry.get("word")
            ko_pos = entry.get("pos") or ""
            en_pos = POS_KR_EN.get(ko_pos, "")
            if not ko_headword:
                continue

            for sense in entry.get("senses", []):
                total_senses += 1
                ko_def = sense.get("definition")
                if not ko_def:
                    continue

                translations = sense.get("translations", [])
                example_ko = None
                examples = sense.get("examples") or []
                if examples:
                    example_ko = examples[0]

                hit = False
                for tr in translations:
                    en_word_raw = tr.get("word") or ""
                    for en_lemma in normalize_english_phrase(en_word_raw):
                        if en_lemma in oewn_lemmas:
                            lemma_set.add(en_lemma)
                            rows.append((
                                en_lemma,
                                en_pos,
                                ko_def,
                                None,
                                example_ko,
                                ko_headword,
                            ))
                            hit = True
                            matched_senses += 1
                        else:
                            unmatched_english += 1
                if not hit and translations:
                    # translations existed but none matched OEWN
                    pass

    print(
        f"[build-kr-api] entries={total_entries:,} senses={total_senses:,} "
        f"en-matched={matched_senses:,} en-unmatched={unmatched_english:,} "
        f"unique-en-lemmas={len(lemma_set):,}"
    )

    cur.executemany(
        "INSERT OR IGNORE INTO lemma(lemma, source_mask) VALUES(?, ?)",
        [(lem, SOURCE_BIT_KRDICT) for lem in lemma_set],
    )
    cur.executemany(
        "INSERT INTO sense_kr(lemma, pos, gloss_ko, example_en, example_ko, krdict_headword) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM sense_kr")
    print(f"[build-kr-api] sense_kr rows: {cur.fetchone()[0]:,}")
    conn.close()

    print(f"[build-kr-api] wrote {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("usage: build_krdict_from_api.py <dump.jsonl> <oewn.sqlite> <out.sqlite>",
              file=sys.stderr)
        sys.exit(2)
    build(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
