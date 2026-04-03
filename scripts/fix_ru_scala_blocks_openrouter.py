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
PROMPT_FILE = ROOT / "prompts" / "ru_scala_code_qc_system.txt"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "x-ai/grok-4.1-fast"
APP_TITLE = "Functional Programming in Scala RU Scala-Block QC"
APP_URL = "https://github.com/exoldy/functional-programming-in-scala-second"

OUTER_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*)\n```[ \t]*$", re.DOTALL)
FENCE_START_RE = re.compile(r"^```([^\n`]*)$")

TRIPLE_STRING_RE = re.compile(r'"""(?:.|\\n)*?"""', re.DOTALL)
DOUBLE_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')
CHAR_RE = re.compile(r"'(?:\\.|[^'\\])'")
LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)
BLOCK_COMMENT_RE = re.compile(r"/\\*(?:.|\\n)*?\\*/", re.DOTALL)
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER_RE = re.compile(r"(?:0x[0-9A-Fa-f]+|\\d+(?:\\.\\d+)?)")
OP_RE = re.compile(r"=>|<-|::|[=:+\\-*/<>!|&%^~?.@#]+")
PUNCT_RE = re.compile(r"[()\\[\\]{},;]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fix Scala fenced code blocks in RU markdown pages.")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--pages", nargs="*", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def load_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


def collect_files(args: argparse.Namespace) -> list[str]:
    files = list(args.pages)
    if args.all:
        files.extend(path.name for path in sorted(PAGES_DIR.glob("page-*.md")))
    files = sorted(dict.fromkeys(files))
    if args.limit is not None:
        files = files[: args.limit]
    return files


def strip_outer_fence(text: str) -> str:
    stripped = text.strip()
    match = OUTER_FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip() + "\n"
    return stripped + ("\n" if not stripped.endswith("\n") else "")


def reserve_token(prefix: str, counter: dict[str, int], placeholders: dict[str, str], content: str) -> str:
    counter["value"] += 1
    token = f"⟪KEEP_{prefix}_{counter['value']:04d}⟫"
    placeholders[token] = content
    return token


def mask_except_scala(markdown_text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    counter = {"value": 0}
    lines = markdown_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        match = FENCE_START_RE.match(line.rstrip("\n"))
        if match:
            info = match.group(1).strip()
            block_lines = [line]
            i += 1
            while i < len(lines):
                block_lines.append(lines[i])
                if lines[i].startswith("```"):
                    i += 1
                    break
                i += 1
            block = "".join(block_lines)
            if info.startswith("scala"):
                out.append(block)
            else:
                out.append(reserve_token("BLOCK", counter, placeholders, block))
            continue

        chunk_lines = []
        while i < len(lines):
            next_match = FENCE_START_RE.match(lines[i].rstrip("\n"))
            if next_match:
                break
            chunk_lines.append(lines[i])
            i += 1
        chunk = "".join(chunk_lines)
        if chunk:
            out.append(reserve_token("BLOCK", counter, placeholders, chunk))

    return "".join(out), placeholders


def restore_placeholders(text: str, placeholders: dict[str, str]) -> str:
    restored = text
    for token, original in placeholders.items():
        restored = restored.replace(token, original)
    return restored


def extract_scala_blocks(markdown_text: str) -> list[str]:
    lines = markdown_text.splitlines(keepends=True)
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        match = FENCE_START_RE.match(lines[i].rstrip("\n"))
        if match:
            info = match.group(1).strip()
            block_lines = [lines[i]]
            i += 1
            while i < len(lines):
                block_lines.append(lines[i])
                if lines[i].startswith("```"):
                    i += 1
                    break
                i += 1
            if info.startswith("scala"):
                blocks.append("".join(block_lines))
            continue
        i += 1
    return blocks


def scala_tokenize(code: str) -> list[str]:
    text = code
    tokens: list[str] = []
    index = 0
    patterns = [
        TRIPLE_STRING_RE,
        DOUBLE_STRING_RE,
        CHAR_RE,
        BLOCK_COMMENT_RE,
        LINE_COMMENT_RE,
        IDENT_RE,
        NUMBER_RE,
        OP_RE,
        PUNCT_RE,
    ]

    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        matched = False
        for pattern in patterns:
            match = pattern.match(text, index)
            if match:
                tokens.append(match.group(0))
                index = match.end()
                matched = True
                break
        if not matched:
            tokens.append(text[index])
            index += 1
    return tokens


def scala_body_from_block(block: str) -> str:
    lines = block.splitlines()
    if len(lines) < 2:
        return ""
    if lines[-1].startswith("```"):
        return "\n".join(lines[1:-1])
    return "\n".join(lines[1:])


def validate_scala_blocks(original_md: str, fixed_md: str, rel_name: str) -> None:
    original_blocks = extract_scala_blocks(original_md)
    fixed_blocks = extract_scala_blocks(fixed_md)
    if len(original_blocks) != len(fixed_blocks):
        raise RuntimeError(
            f"{rel_name}: scala block count changed: {len(original_blocks)} -> {len(fixed_blocks)}"
        )

    for idx, (old_block, new_block) in enumerate(zip(original_blocks, fixed_blocks), start=1):
        old_body = scala_body_from_block(old_block)
        new_body = scala_body_from_block(new_block)
        if scala_tokenize(old_body) != scala_tokenize(new_body):
            raise RuntimeError(f"{rel_name}: scala block {idx} changed non-whitespace tokens")


def request_fix(api_key: str, model: str, prompt: str, rel_name: str, masked_md: str) -> str:
    body = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "\n".join(
                    [
                        "Исправь только форматирование fenced scala code blocks.",
                        "- Чини только отступы, переводы строк и layout Scala 3.",
                        "- Не меняй не-whitespace токены кода.",
                        "- Не трогай токены ⟪KEEP_*⟫.",
                        "- Верни весь markdown документ целиком.",
                        "",
                        f"Файл: {rel_name}",
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


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("Missing OPENROUTER_API_KEY or --api-key.")

    files = collect_files(args)
    if not files:
        raise SystemExit("No files selected.")

    prompt = load_prompt()

    for name in files:
        path = PAGES_DIR / name
        if not path.exists():
            print(f"skip missing {name}")
            continue

        original = path.read_text(encoding="utf-8")
        if "```scala" not in original:
            print(f"skip no-scala {name}")
            continue

        print(f"fix-scala {name}")
        masked, placeholders = mask_except_scala(original)
        fixed = request_fix(args.api_key, args.model, prompt, name, masked)
        fixed = strip_outer_fence(fixed)
        missing = [token for token in placeholders if token not in fixed]
        if missing:
            raise RuntimeError(f"{name}: model damaged placeholders: {missing[:5]}")
        restored = restore_placeholders(fixed, placeholders)
        validate_scala_blocks(original, restored, name)

        if args.overwrite or restored != original:
            path.write_text(restored, encoding="utf-8")
        time.sleep(0.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
