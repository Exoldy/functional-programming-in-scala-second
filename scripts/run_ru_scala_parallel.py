#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "docs" / "ru" / "pages"
FIXER = ROOT / "scripts" / "fix_ru_scala_blocks_openrouter.py"
STATE_PATH = ROOT / ".translation-cache" / "openrouter-ru" / "scala_qc_state.json"
FAILURES_PATH = ROOT / ".translation-cache" / "openrouter-ru" / "scala_qc_failures.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scala code-block QC in parallel.")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"))
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-completed", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_targets(include_completed: bool, limit: int | None) -> list[str]:
    state = load_json(STATE_PATH, {"completed": []})
    completed = set(state.get("completed", []))
    all_pages = sorted(
        path.name for path in PAGES_DIR.glob("page-*.md") if "```scala" in path.read_text(encoding="utf-8")
    )
    targets = all_pages if include_completed else [name for name in all_pages if name not in completed]
    if limit is not None:
        targets = targets[:limit]
    return targets


def run_one(page: str, api_key: str, timeout_seconds: int, overwrite: bool) -> tuple[str, bool, str]:
    cmd = [
        sys.executable,
        str(FIXER),
        "--pages",
        page,
        "--api-key",
        api_key,
    ]
    if overwrite:
        cmd.append("--overwrite")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        return page, False, f"timeout>{timeout_seconds}s"

    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        return page, False, message[:4000]
    return page, True, ""


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("Missing OPENROUTER_API_KEY or --api-key.")

    targets = collect_targets(args.include_completed, args.limit)
    if not targets:
        print("No target pages.")
        return

    state = load_json(STATE_PATH, {"completed": []})
    completed = set(state.get("completed", []))
    completed_lock = threading.Lock()
    failure_map = {item["file"]: item for item in load_json(FAILURES_PATH, []) if "file" in item}
    failure_lock = threading.Lock()

    attempts: dict[str, int] = {page: 0 for page in targets}
    queue = list(targets)
    in_flight = {}
    total = len(targets)
    done_ok = 0
    done_fail = 0

    def submit_next(executor: ThreadPoolExecutor) -> None:
        if not queue:
            return
        page = queue.pop(0)
        attempts[page] += 1
        future = executor.submit(run_one, page, args.api_key, args.timeout_seconds, args.overwrite)
        in_flight[future] = page
        print(
            f"start {page} attempt={attempts[page]} inflight={len(in_flight)} remaining={len(queue)}",
            flush=True,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for _ in range(min(args.workers, len(queue))):
            submit_next(executor)

        while in_flight:
            finished, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
            for future in finished:
                page = in_flight.pop(future)
                result_page, ok, error = future.result()
                if ok:
                    with completed_lock:
                        completed.add(result_page)
                        state["completed"] = sorted(completed)
                        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        save_json(STATE_PATH, state)
                    with failure_lock:
                        failure_map.pop(result_page, None)
                        save_json(FAILURES_PATH, sorted(failure_map.values(), key=lambda item: item["file"]))
                    done_ok += 1
                    print(f"ok {result_page} ok={done_ok}/{total} fail={done_fail}", flush=True)
                else:
                    if attempts[result_page] <= args.retries:
                        queue.append(result_page)
                        print(f"retry {result_page} attempt={attempts[result_page]} reason={error[:160]}", flush=True)
                    else:
                        with failure_lock:
                            failure_map[result_page] = {"file": result_page, "error": error}
                            save_json(FAILURES_PATH, sorted(failure_map.values(), key=lambda item: item["file"]))
                        done_fail += 1
                        print(f"fail {result_page} ok={done_ok}/{total} fail={done_fail}", flush=True)

                while len(in_flight) < args.workers and queue:
                    submit_next(executor)

    print(
        f"done targets={total} completed_now={done_ok} failed_now={done_fail} outstanding_failures={len(failure_map)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
