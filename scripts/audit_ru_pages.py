#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "docs" / "ru" / "pages"
REPORT_PATH = ROOT / ".translation-cache" / "openrouter-ru" / "audit_ru_pages.json"

CODE_FENCE_RE = re.compile(r"^```")
LATIN_WORD_RE = re.compile(r"[A-Za-z]{4,}")
CYR_WORD_RE = re.compile(r"[А-Яа-яЁё]{4,}")


def analyze_page(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_code = False
    latin_words = 0
    cyr_words = 0
    long_lines = 0
    suspicious_samples: list[str] = []
    index_like = False

    for line in lines:
        if CODE_FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        if "> индекс /" in line.lower() or "> указатель /" in line.lower():
            index_like = True
        if len(line) >= 220:
            long_lines += 1
        if line.startswith(("![", "[", "> ")):
            continue
        latin = LATIN_WORD_RE.findall(line)
        cyr = CYR_WORD_RE.findall(line)
        latin_words += len(latin)
        cyr_words += len(cyr)
        if len(line) >= 180 or len(latin) >= 8:
            suspicious_samples.append(line[:220])

    flags: list[str] = []
    if index_like:
        flags.append("index_like")
    if latin_words >= 20 and latin_words > max(8, int(cyr_words * 0.6)):
        flags.append("english_heavy")
    if long_lines >= 3:
        flags.append("long_lines")
    if index_like and long_lines >= 1:
        flags.append("index_needs_reflow")

    return {
        "file": path.name,
        "latin_words": latin_words,
        "cyr_words": cyr_words,
        "long_lines": long_lines,
        "flags": flags,
        "samples": suspicious_samples[:5],
    }


def main() -> None:
    report = [analyze_page(path) for path in sorted(PAGES_DIR.glob("page-*.md"))]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    flagged = [item for item in report if item["flags"]]
    print(f"report={REPORT_PATH}")
    print(f"pages={len(report)} flagged={len(flagged)}")
    for item in flagged[:40]:
        print(item["file"], ",".join(item["flags"]))


if __name__ == "__main__":
    main()

