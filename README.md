# bookear-dict-data

Offline English-English + English-Korean dictionary data used by the Bookear iOS app.

Ships as a single SQLite file (`bookear-dict-v1.sqlite`, ~43 MB) combining:

- **Open English WordNet 2025** — CC BY 4.0 — 127,311 English lemmas, 185,129 senses
- **한국어-영어 학습사전 (KRDict)** — CC BY-SA 2.0 KR — 49,870 English lemmas indexed to 108,819 Korean senses (reverse-indexed from the native Korean→foreign direction)

**159,530 distinct English lemmas total**, of which 17,651 are covered by both sources.

See [NOTICE](./NOTICE) and [LICENSE](./LICENSE) for attribution chain and licensing. This repository exists in part to satisfy the CC BY-SA share-alike obligation: the processed SQLite is redistributable from here even when the iOS app is delivered through App Store FairPlay DRM.

## Schema

```sql
CREATE TABLE lemma (
    lemma       TEXT PRIMARY KEY,
    phonetic    TEXT,           -- nullable; OEWN doesn't ship IPA
    source_mask INTEGER NOT NULL  -- bit 0: OEWN, bit 1: KRDict
);

CREATE TABLE sense_en (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma   TEXT NOT NULL,
    pos     TEXT,
    gloss   TEXT NOT NULL,
    example TEXT
);
CREATE INDEX idx_sense_en_lemma ON sense_en(lemma);

CREATE TABLE sense_kr (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma           TEXT NOT NULL,
    pos             TEXT,
    gloss_ko        TEXT NOT NULL,
    example_en      TEXT,   -- KRDict has no English example translations
    example_ko      TEXT,
    krdict_headword TEXT NOT NULL
);
CREATE INDEX idx_sense_kr_lemma ON sense_kr(lemma);
```

## Reproducing the build

Requires Python 3.11+ and `lxml` (for tolerant KRDict XML parsing).

```bash
# 1. OEWN 2025
curl -L -o data/oewn-2025.xml.gz https://en-word.net/static/english-wordnet-2025.xml.gz
gunzip -k data/oewn-2025.xml.gz

# 2. KRDict (via GitHub mirror)
git clone --depth 1 https://github.com/spellcheck-ko/korean-dict-nikl-krdict data/krdict

# 3. Python env
python3 -m venv .venv && .venv/bin/pip install lxml

# 4. Build
.venv/bin/python scripts/build_oewn.py   data/oewn-2025.xml build/oewn.sqlite
.venv/bin/python scripts/build_krdict.py data/krdict        build/krdict.sqlite
.venv/bin/python scripts/merge.py        build/oewn.sqlite build/krdict.sqlite build/bookear-dict-v1.sqlite
```

## Sample queries

```sql
SELECT gloss FROM sense_en WHERE lemma='astonishment';
-- the feeling that accompanies something extremely surprising

SELECT gloss_ko, krdict_headword FROM sense_kr WHERE lemma='astonishment';
-- 숨이 막힐 듯이 갑자기 놀라거나 겁에 질림.            기겁
-- 어떤 일이 뜻밖이거나 훌륭하거나 …                   놀라움
-- 어떤 일이 뜻밖이거나 훌륭하거나 …                   놀람
```

## Audio files

KRDict XML references pronunciation `.wav` URLs. **These have been stripped** from the output SQLite. Per NIKL policy, the audio files are not redistributable even though the text corpus is. The Bookear app uses system TTS as a substitute.

## Coverage notes

- KRDict is a *Basic Korean Dictionary* (한국어기초사전) so uncommon English words like "serendipity" appear only under OEWN.
- Any word in OEWN returns a monolingual English definition even if no Korean equivalent exists — the UI should fall back gracefully.
