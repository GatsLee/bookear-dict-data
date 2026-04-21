#!/usr/bin/env python3
"""
Merge build/oewn.sqlite + build/krdict.sqlite → build/bookear-dict-v1.sqlite

Schema:
  lemma(lemma PRIMARY KEY, phonetic, source_mask)
  sense_en(id, lemma, pos, gloss, example)
  sense_kr(id, lemma, pos, gloss_ko, example_en, example_ko, krdict_headword)

source_mask bits:
  0b01 = OEWN present
  0b10 = KRDict present
  0b11 = both

Usage:
  python3 merge.py build/oewn.sqlite build/krdict.sqlite build/bookear-dict-v1.sqlite
"""

import sys
import sqlite3
from pathlib import Path


def merge(oewn_path: Path, krdict_path: Path, out_path: Path) -> None:
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(out_path)
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
        CREATE TABLE sense_kr (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lemma TEXT NOT NULL,
            pos TEXT,
            gloss_ko TEXT NOT NULL,
            example_en TEXT,
            example_ko TEXT,
            krdict_headword TEXT NOT NULL
        );
        CREATE INDEX idx_sense_en_lemma ON sense_en(lemma);
        CREATE INDEX idx_sense_kr_lemma ON sense_kr(lemma);
    """)

    cur.execute(f"ATTACH DATABASE '{oewn_path}' AS oewn")
    cur.execute(f"ATTACH DATABASE '{krdict_path}' AS krdict")

    # 1) Copy EN senses
    cur.execute(
        "INSERT INTO sense_en(lemma, pos, gloss, example) "
        "SELECT lemma, pos, gloss, example FROM oewn.sense_en"
    )
    en_count = cur.rowcount
    print(f"[merge] copied sense_en: {en_count:,}")
    conn.commit()

    # 2) Copy KR senses
    cur.execute(
        "INSERT INTO sense_kr(lemma, pos, gloss_ko, example_en, example_ko, krdict_headword) "
        "SELECT lemma, pos, gloss_ko, example_en, example_ko, krdict_headword "
        "FROM krdict.sense_kr"
    )
    kr_count = cur.rowcount
    print(f"[merge] copied sense_kr: {kr_count:,}")
    conn.commit()

    # 3) Pull lemma rows into Python, then merge source_mask bits by lemma
    cur.execute("SELECT lemma, source_mask FROM oewn.lemma")
    oewn_lemmas = cur.fetchall()
    cur.execute("SELECT lemma, source_mask FROM krdict.lemma")
    krdict_lemmas = cur.fetchall()
    conn.commit()

    masks = {}
    for lemma, mask in oewn_lemmas:
        masks[lemma] = masks.get(lemma, 0) | mask
    for lemma, mask in krdict_lemmas:
        masks[lemma] = masks.get(lemma, 0) | mask

    cur.executemany(
        "INSERT INTO lemma(lemma, source_mask) VALUES(?, ?)",
        [(lemma, mask) for lemma, mask in masks.items()],
    )
    conn.commit()

    cur.execute("DETACH DATABASE oewn")
    cur.execute("DETACH DATABASE krdict")

    # 4) Stats
    cur.execute("SELECT COUNT(*) FROM lemma")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM lemma WHERE source_mask = 1")
    oewn_only = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM lemma WHERE source_mask = 2")
    krdict_only = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM lemma WHERE source_mask = 3")
    both = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sense_en")
    se = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sense_kr")
    sk = cur.fetchone()[0]

    print(f"[merge] lemma total:        {total:,}")
    print(f"[merge]   OEWN only:        {oewn_only:,}")
    print(f"[merge]   KRDict only:      {krdict_only:,}")
    print(f"[merge]   both:             {both:,}")
    print(f"[merge] sense_en rows:      {se:,}")
    print(f"[merge] sense_kr rows:      {sk:,}")

    # Vacuum to compact
    cur.execute("VACUUM")
    conn.commit()
    conn.close()

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"[merge] wrote {out_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("usage: merge.py <oewn.sqlite> <krdict.sqlite> <out.sqlite>", file=sys.stderr)
        sys.exit(2)
    merge(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
