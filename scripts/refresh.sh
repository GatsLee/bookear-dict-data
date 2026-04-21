#!/usr/bin/env bash
#
# End-to-end refresh from the current state of data/ into a finished bundle.
#
# Assumes that:
#   - data/oewn-2025.xml       exists
#   - data/krdict/             has the 2021 mirror XMLs (for target_code list)
#   - data/krdict-api-dump.jsonl  is either complete or good enough to build
#                              from (the build step tolerates partial data)
#   - .venv/ is a Python env with lxml installed
#   - KRDICT_API_KEY is only needed if you want to refresh the dump; skip if
#     already fetched.
#
# Usage:
#   ./scripts/refresh.sh                       # rebuild from whatever's in data/
#   ./scripts/refresh.sh --copy-to-ios         # also copy to iOS Resources/
#   ./scripts/refresh.sh --fetch               # kick off a fresh API dump first

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "error: .venv not found — run 'python3 -m venv .venv && .venv/bin/pip install lxml'" >&2
    exit 2
fi

COPY_TO_IOS=0
FETCH=0
for arg in "$@"; do
    case "$arg" in
        --copy-to-ios) COPY_TO_IOS=1 ;;
        --fetch)       FETCH=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

mkdir -p build

if [ "$FETCH" -eq 1 ]; then
    if [ -z "${KRDICT_API_KEY:-}" ]; then
        echo "error: --fetch requires KRDICT_API_KEY env var" >&2
        exit 2
    fi
    echo "[refresh] step 0/6 — ensure target_codes"
    if [ ! -f data/target_codes.txt ]; then
        "$PY" scripts/extract_target_codes.py data/krdict data/target_codes.txt
    fi

    echo "[refresh] step 1/6 — dumping from NIKL API (resumable)"
    "$PY" scripts/fetch_krdict_api.py \
        --codes data/target_codes.txt \
        --out data/krdict-api-dump.jsonl
fi

echo "[refresh] step 2/6 — OEWN → oewn.sqlite"
"$PY" scripts/build_oewn.py data/oewn-2025.xml build/oewn.sqlite

echo "[refresh] step 3/6 — KRDict dump → krdict.sqlite"
if [ -f data/krdict-api-dump.jsonl ] && [ -s data/krdict-api-dump.jsonl ]; then
    "$PY" scripts/build_krdict_from_api.py \
        data/krdict-api-dump.jsonl \
        build/oewn.sqlite \
        build/krdict.sqlite
    SOURCE="NIKL API ($(wc -l < data/krdict-api-dump.jsonl | tr -d ' ') entries)"
else
    "$PY" scripts/build_krdict.py data/krdict build/krdict.sqlite
    SOURCE="2021 GitHub mirror"
fi
echo "[refresh] KR source: $SOURCE"

echo "[refresh] step 4/6 — merge"
"$PY" scripts/merge.py build/oewn.sqlite build/krdict.sqlite build/bookear-dict-v1.sqlite

echo "[refresh] step 5/6 — stats"
sqlite3 build/bookear-dict-v1.sqlite <<'SQL'
.mode column
.headers on
SELECT
    (SELECT COUNT(*) FROM lemma) AS total_lemmas,
    (SELECT COUNT(*) FROM lemma WHERE source_mask = 1) AS oewn_only,
    (SELECT COUNT(*) FROM lemma WHERE source_mask = 2) AS krdict_only,
    (SELECT COUNT(*) FROM lemma WHERE source_mask = 3) AS both,
    (SELECT COUNT(*) FROM sense_en) AS sense_en_rows,
    (SELECT COUNT(*) FROM sense_kr) AS sense_kr_rows;
SQL

if [ "$COPY_TO_IOS" -eq 1 ]; then
    IOS_DEST="$(dirname "$ROOT")/Bookear/ios/Bookear/Resources/Dictionaries/bookear-dict-v1.sqlite"
    if [ -d "$(dirname "$IOS_DEST")" ]; then
        echo "[refresh] step 6/6 — copy to iOS: $IOS_DEST"
        cp build/bookear-dict-v1.sqlite "$IOS_DEST"
        ls -lh "$IOS_DEST"
    else
        echo "[refresh] step 6/6 — skip iOS copy: $IOS_DEST not found"
    fi
else
    echo "[refresh] step 6/6 — iOS copy skipped (pass --copy-to-ios to enable)"
fi

echo "[refresh] done. output: build/bookear-dict-v1.sqlite"
