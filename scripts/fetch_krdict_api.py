#!/usr/bin/env python3
"""
Dump the entire 한국어기초사전 via the KRDict Open API.

Strategy:
  - Iterate target_code = 1..60000 on the `view` endpoint.
    (Actual max is ~51,947 but we stop after a consecutive run of "no result"
    responses.)
  - For each entry, capture the headword, part-of-speech, senses, definitions,
    English translation(s), and examples.
  - Persist progress incrementally to a JSONL file so interrupted runs can
    resume. Respects the 50,000-calls/day rate limit by pacing.

Usage:
  export KRDICT_API_KEY=<32-hex-chars>
  python3 fetch_krdict_api.py --out ../data/krdict-api-dump.jsonl

API docs: https://krdict.korean.go.kr/eng/openApi/openApiInfo
License of fetched data: CC BY-SA 2.0 KR (© 국립국어원)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    from lxml import etree as ET
except ImportError:
    import xml.etree.ElementTree as ET


API_VIEW = "https://krdict.korean.go.kr/api/view"
DAILY_LIMIT_GUARD = 48_000  # leave headroom below the 50,000 quota
RATE_SLEEP_SEC = 0.12       # ≈ 8 req/s; well within server tolerance
TRANS_LANG_ENGLISH = "1"


def feat(element, attr):
    for child in element:
        if child.tag == "feat" and child.get("att") == attr:
            return child.get("val")
    return None


def fetch_entry(key: str, target_code: int, max_retries: int = 4) -> dict | None:
    """Returns parsed dict for one target_code, or None if not found.

    Retries transient network errors with exponential backoff."""
    params = {
        "key": key,
        "method": "target_code",
        "q": str(target_code),
        "translated": "y",
        "trans_lang": TRANS_LANG_ENGLISH,
    }
    url = f"{API_VIEW}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "bookear-dict-data/1.0"})

    last_error = None
    for attempt in range(max_retries):
        try:
            with urlopen(req, timeout=30) as resp:
                body = resp.read()
            break
        except HTTPError as e:
            return {"_error": f"http {e.code}"}
        except (URLError, TimeoutError, OSError) as e:
            last_error = e
            backoff = min(2 ** attempt, 15)
            print(f"[fetch] tc={target_code} retry {attempt+1}/{max_retries} after {backoff}s: {e}",
                  file=sys.stderr)
            time.sleep(backoff)
    else:
        return {"_error": f"url {last_error}"}

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return {"_error": f"parse {e}"}

    # API returns <error><error_code>...</error_code></error> on problems
    err = root.find(".//error_code")
    if err is not None:
        code = err.text or ""
        if code in ("050",):  # no result
            return None
        return {"_error": f"api {code}"}

    item = root.find(".//item")
    if item is None:
        return None

    def t(root_el, path):
        el = root_el.find(path)
        return el.text if el is not None and el.text is not None else None

    word_info = item.find("word_info")
    if word_info is None:
        word_info = item

    entry = {
        "target_code": target_code,
        "word": t(word_info, "word"),
        "word_unit": t(word_info, "word_unit"),
        "pos": t(word_info, "pos"),
        "pronunciation": None,
        "word_grade": t(word_info, "word_grade"),
        "senses": [],
    }

    pron = word_info.find(".//pronunciation_info/pronunciation")
    if pron is not None and pron.text:
        entry["pronunciation"] = pron.text

    for sense in word_info.findall(".//sense_info"):
        sdef = (sense.findtext("definition") or "").strip() or None
        examples = []
        for ex in sense.findall(".//example_info"):
            ex_text = (ex.findtext("example") or "").strip()
            if ex_text:
                examples.append(ex_text)
        translations = []
        for tr in sense.findall(".//translation"):
            trans_lang = (tr.findtext("trans_lang") or "").strip()
            # Filter to English. `trans_lang=1` should already filter at API,
            # but double-check because some responses include multiple.
            if trans_lang and trans_lang != "영어":
                continue
            trans_word = (tr.findtext("trans_word") or "").strip() or None
            trans_dfn = (tr.findtext("trans_dfn") or "").strip() or None
            if trans_word or trans_dfn:
                translations.append({"word": trans_word, "definition": trans_dfn})
        entry["senses"].append({
            "definition": sdef,
            "examples": examples,
            "translations": translations,
        })

    return entry


def already_fetched(out_path: Path) -> set[int]:
    if not out_path.exists():
        return set()
    seen = set()
    with out_path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
                if "target_code" in rec:
                    seen.add(rec["target_code"])
            except json.JSONDecodeError:
                continue
    return seen


def run(out_path: Path, codes: list[int], key: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen = already_fetched(out_path)
    total = len(codes)
    pending = [c for c in codes if c not in seen]
    print(f"[fetch] total={total:,} already={len(seen):,} pending={len(pending):,}")

    calls_this_run = 0
    collected = 0
    missing = 0

    with out_path.open("a") as f:
        for tc in pending:
            if calls_this_run >= DAILY_LIMIT_GUARD:
                print(f"[fetch] stopping at daily limit guard ({calls_this_run} calls)")
                break

            try:
                entry = fetch_entry(key, tc)
            except Exception as e:
                print(f"[fetch] tc={tc} unexpected: {e}", file=sys.stderr)
                entry = {"_error": f"unhandled {e}"}
            calls_this_run += 1

            if entry is None:
                missing += 1
            elif "_error" in entry:
                print(f"[fetch] tc={tc} error: {entry['_error']}", file=sys.stderr)
                if entry["_error"].startswith("api 010"):
                    print("[fetch] daily quota exceeded — stopping")
                    break
                # Otherwise swallow and continue with next target_code.
            else:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                collected += 1

            if calls_this_run % 500 == 0:
                print(
                    f"[fetch] progress: calls={calls_this_run:,} "
                    f"collected={collected:,} missing={missing:,} "
                    f"next_tc={tc}",
                    flush=True,
                )

            time.sleep(RATE_SLEEP_SEC)

    print(f"[fetch] done: calls={calls_this_run:,} collected={collected:,} missing={missing:,}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="output JSONL path")
    p.add_argument("--codes", required=True,
                   help="file with one target_code per line")
    args = p.parse_args()

    key = os.environ.get("KRDICT_API_KEY", "").strip()
    if not key:
        print("error: set env var KRDICT_API_KEY=<your-32-hex-key>", file=sys.stderr)
        sys.exit(2)
    if len(key) < 16:
        print("error: KRDICT_API_KEY looks too short", file=sys.stderr)
        sys.exit(2)

    codes_path = Path(args.codes)
    if not codes_path.exists():
        print(f"error: codes file not found: {codes_path}", file=sys.stderr)
        sys.exit(2)
    codes = [int(line.strip()) for line in codes_path.read_text().splitlines()
             if line.strip().isdigit()]
    codes.sort()

    run(Path(args.out), codes, key)


if __name__ == "__main__":
    main()
