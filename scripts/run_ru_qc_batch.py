#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "docs" / "ru" / "pages"
STATE_PATH = ROOT / ".translation-cache" / "openrouter-ru" / "qc_state.json"
FAILURES_PATH = ROOT / ".translation-cache" / "openrouter-ru" / "qc_failures.json"
FIXER = ROOT / "scripts" / "fix_ru_pages_openrouter.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RU markdown QC page-by-page with timeout.")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"))
    parser.add_argument("--start-from", default="page-0001.md")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("Missing OPENROUTER_API_KEY or --api-key.")

    all_pages = sorted(path.name for path in PAGES_DIR.glob("page-*.md"))
    all_pages = [name for name in all_pages if name >= args.start_from]
    if args.limit is not None:
        all_pages = all_pages[: args.limit]

    state = load_json(STATE_PATH, {"completed": [], "updated_at": None})
    completed = set(state.get("completed", []))
    failures: list[dict[str, str]] = []

    for page in all_pages:
        if page in completed and not args.overwrite:
            print(f"skip {page}")
            continue

        print(f"run {page}", flush=True)
        cmd = [
            sys.executable,
            str(FIXER),
            "--pages",
            page,
            "--overwrite",
            "--api-key",
            args.api_key,
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(ROOT),
                timeout=args.timeout_seconds,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired:
            failures.append({"file": page, "error": f"timeout>{args.timeout_seconds}s"})
            save_json(FAILURES_PATH, failures)
            print(f"timeout {page}", flush=True)
            continue

        if result.returncode != 0:
            failures.append(
                {
                    "file": page,
                    "error": (result.stderr or result.stdout).strip()[:2000],
                }
            )
            save_json(FAILURES_PATH, failures)
            print(f"fail {page}", flush=True)
            continue

        completed.add(page)
        state["completed"] = sorted(completed)
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_json(STATE_PATH, state)
        print(f"ok {page}", flush=True)

    save_json(FAILURES_PATH, failures)
    print(
        f"done completed={len(completed)} attempted={len(all_pages)} failed={len(failures)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
