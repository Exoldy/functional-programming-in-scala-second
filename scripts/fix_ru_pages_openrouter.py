#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "docs" / "ru" / "pages"
PROMPT_FILE = ROOT / "prompts" / "ru_markdown_qc_system.txt"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "x-ai/grok-4.1-fast"
APP_TITLE = "Functional Programming in Scala RU QC"
APP_URL = "https://github.com/exoldy/functional-programming-in-scala-second"

FENCED_CODE_RE = re.compile(r"(^```[^\n]*\n.*?^```[ \t]*$)", re.MULTILINE | re.DOTALL)
MARKDOWN_LINK_RE = re.compile(r"(!?\[[^\]]*])\(([^)]+)\)")
RAW_URL_RE = re.compile(r"https?://[^\s)>\"]+")
OUTER_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*)\n```[ \t]*$", re.DOTALL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fix formatting/translation defects in RU pages.")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--pages", nargs="*", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--from-audit", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def load_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


def mask_segments(markdown_text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    counter = 0

    def reserve(prefix: str, content: str) -> str:
        nonlocal counter
        counter += 1
        token = f"⟪KEEP_{prefix}_{counter:04d}⟫"
        placeholders[token] = content
        return token

    text = FENCED_CODE_RE.sub(lambda m: reserve("CODEBLOCK", m.group(0)), markdown_text)

    def replace_markdown_link(match: re.Match[str]) -> str:
        return reserve("MDLINK", match.group(0))

    text = MARKDOWN_LINK_RE.sub(replace_markdown_link, text)
    text = RAW_URL_RE.sub(lambda m: reserve("RAWURL", m.group(0)), text)
    return text, placeholders


def restore_segments(markdown_text: str, placeholders: dict[str, str]) -> str:
    restored = markdown_text
    for token, original in placeholders.items():
        restored = restored.replace(token, original)
    return restored


def strip_outer_fence(text: str) -> str:
    stripped = text.strip()
    match = OUTER_FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip() + "\n"
    return stripped + ("\n" if not stripped.endswith("\n") else "")


def request_fix(api_key: str, model: str, prompt: str, label: str, masked_md: str) -> str:
    body = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "\n".join(
                    [
                        "Приведи эту markdown-страницу в порядок.",
                        "- Чини форматирование и переводы строк.",
                        "- Указатель/индекс разнеси в читаемые строки.",
                        "- Остатки английского переведи.",
                        "- Если англицизм нужен, оставляй английский в скобках рядом с русским термином.",
                        "- Не меняй токены вида ⟪KEEP_*⟫.",
                        "",
                        f"Файл: {label}",
                        "",
                        masked_md,
                    ]
                ),
            },
        ],
    }
    raw = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": APP_URL,
        "X-OpenRouter-Title": APP_TITLE,
    }
    request = urllib.request.Request(OPENROUTER_URL, data=raw, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def collect_from_audit() -> list[str]:
    report_path = ROOT / ".translation-cache" / "openrouter-ru" / "audit_ru_pages.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return [item["file"] for item in report if item["flags"]]


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("Missing OPENROUTER_API_KEY or --api-key.")

    files = list(args.pages)
    if args.all:
        files.extend(path.name for path in sorted(PAGES_DIR.glob("page-*.md")))
    if args.from_audit:
        files.extend(collect_from_audit())
    files = sorted(dict.fromkeys(files))
    if args.limit is not None:
        files = files[: args.limit]
    if not files:
        raise SystemExit("No files selected for fixing.")

    prompt = load_prompt()

    for name in files:
        path = PAGES_DIR / name
        if not path.exists():
            print(f"skip missing {name}")
            continue
        print(f"fix {name}")
        original = path.read_text(encoding="utf-8")
        masked, placeholders = mask_segments(original)
        fixed = request_fix(args.api_key, args.model, prompt, name, masked)
        fixed = strip_outer_fence(fixed)
        missing = [token for token in placeholders if token not in fixed]
        if missing:
            raise RuntimeError(f"{name}: model damaged placeholders: {missing[:5]}")
        restored = restore_segments(fixed, placeholders)
        if args.overwrite or restored != original:
            path.write_text(restored, encoding="utf-8")
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
