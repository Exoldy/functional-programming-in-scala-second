#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
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

TRIPLE_STRING_RE = re.compile(r'"""(?:.|\n)*?"""', re.DOTALL)
DOUBLE_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')
CHAR_RE = re.compile(r"'(?:\\.|[^'\\])'")
LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)
BLOCK_COMMENT_RE = re.compile(r"/\*(?:.|\n)*?\*/", re.DOTALL)
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER_RE = re.compile(r"(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)")
OP_RE = re.compile(r"=>|<-|::|[=:+\-*/<>!|&%^~?.@#]+")
PUNCT_RE = re.compile(r"[()\[\]{},;]")


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


def split_parts(markdown_text: str) -> list[tuple[str, str]]:
    lines = markdown_text.splitlines(keepends=True)
    parts: list[tuple[str, str]] = []
    text_chunk: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        match = FENCE_START_RE.match(line.rstrip("\n"))
        if match:
            if text_chunk:
                parts.append(("text", "".join(text_chunk)))
                text_chunk = []

            block_lines = [line]
            info = match.group(1).strip()
            index += 1
            while index < len(lines):
                block_lines.append(lines[index])
                if lines[index].startswith("```"):
                    index += 1
                    break
                index += 1
            block = "".join(block_lines)
            block_type = "scala" if info.startswith("scala") else "fence"
            parts.append((block_type, block))
            continue

        text_chunk.append(line)
        index += 1

    if text_chunk:
        parts.append(("text", "".join(text_chunk)))
    return parts


def extract_scala_blocks(markdown_text: str) -> list[str]:
    return [content for kind, content in split_parts(markdown_text) if kind == "scala"]


def strip_outer_fence(text: str) -> str:
    stripped = text.strip()
    match = OUTER_FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip() + "\n"
    return stripped + ("\n" if not stripped.endswith("\n") else "")


def scala_body_from_block(block: str) -> str:
    lines = block.splitlines()
    if len(lines) < 2:
        return ""
    if lines[-1].startswith("```"):
        return "\n".join(lines[1:-1])
    return "\n".join(lines[1:])


def is_repl_block(block: str) -> bool:
    for line in scala_body_from_block(block).splitlines():
        if line.strip():
            return line.lstrip().startswith("scala>")
    return False


def should_skip_block(block: str) -> tuple[bool, str]:
    body = scala_body_from_block(block)
    stripped_lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not stripped_lines:
        return True, "empty"
    if is_repl_block(block):
        return True, "repl"
    if "➥" in body:
        return True, "trace"
    if body.lstrip().startswith("/**"):
        return True, "doc-comment"
    if "extesnion" in body:
        return True, "ocr-typo"
    if body.count("def ") > 1:
        return True, "multi-def"
    if " . ==" in body:
        return True, "theorem-snippet"
    last_line = stripped_lines[-1]
    if last_line.endswith(":"):
        return True, "truncated-colon"
    if body.count("(") != body.count(")"):
        return True, "unbalanced-parens"
    return False, ""


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


def request_fix(api_key: str, model: str, prompt: str, rel_name: str, block_index: int, block: str) -> str:
    body = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "\n".join(
                    [
                        "Исправь только форматирование одного fenced scala code block.",
                        "- Верни ровно один fenced `scala` block.",
                        "- Меняй только пробелы, отступы и переводы строк.",
                        "- Не меняй никакие не-whitespace токены.",
                        "- Если это REPL/diagnostic snippet с `scala>`, не переписывай его как обычный исходник.",
                        "",
                        f"Файл: {rel_name}",
                        f"Блок: {block_index}",
                        "",
                        block,
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


def normalize_scala_block(candidate: str, original: str) -> str:
    body = strip_outer_fence(candidate)
    first_line = original.splitlines(keepends=True)[0]
    if not first_line.endswith("\n"):
        first_line = first_line + "\n"
    return f"{first_line}{body}```\n"


def validate_block(original_block: str, fixed_block: str, rel_name: str, block_index: int) -> None:
    original_tokens = scala_tokenize(scala_body_from_block(original_block))
    fixed_tokens = scala_tokenize(scala_body_from_block(fixed_block))
    if original_tokens != fixed_tokens:
        raise RuntimeError(f"{rel_name}: scala block {block_index} changed non-whitespace tokens")


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
        parts = split_parts(original)
        scala_count = sum(1 for kind, _ in parts if kind == "scala")
        if scala_count == 0:
            print(f"skip no-scala {name}")
            continue

        updated_parts: list[tuple[str, str]] = []
        block_index = 0
        print(f"fix-scala {name} blocks={scala_count}")
        for kind, content in parts:
            if kind != "scala":
                updated_parts.append((kind, content))
                continue

            block_index += 1
            skip, reason = should_skip_block(content)
            if skip:
                updated_parts.append((kind, content))
                print(f"skip {reason} {name}#{block_index}")
                continue
            response = request_fix(args.api_key, args.model, prompt, name, block_index, content)
            normalized = normalize_scala_block(response, content)
            validate_block(content, normalized, name, block_index)
            updated_parts.append((kind, normalized))
            time.sleep(0.05)

        restored = "".join(content for _, content in updated_parts)
        if args.overwrite and restored != original:
            path.write_text(restored, encoding="utf-8")
            print(f"wrote {name}")
        else:
            print(f"checked {name}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
