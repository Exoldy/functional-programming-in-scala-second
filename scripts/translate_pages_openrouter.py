#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
SOURCE_INDEX = DOCS_DIR / "index.md"
SOURCE_PAGES_DIR = DOCS_DIR / "pages"
SOURCE_ASSETS_DIR = SOURCE_PAGES_DIR / "assets"
OUTPUT_ROOT = DOCS_DIR / "ru"
OUTPUT_PAGES_DIR = OUTPUT_ROOT / "pages"
OUTPUT_ASSETS_DIR = OUTPUT_PAGES_DIR / "assets"
PROMPT_FILE = ROOT / "prompts" / "translate_ru_openrouter_system.txt"
CACHE_DIR = ROOT / ".translation-cache" / "openrouter-ru"
STATE_FILE = CACHE_DIR / "state.json"
FAILURES_FILE = CACHE_DIR / "failures.json"
DEFAULT_MODEL = "x-ai/grok-4.1-fast"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
APP_TITLE = "Functional Programming in Scala RU Translator"
APP_URL = "https://github.com/exoldy/functional-programming-in-scala-second"
PIPELINE_VERSION = "2026-04-03c"

FENCED_CODE_RE = re.compile(r"(^```[^\n]*\n.*?^```[ \t]*$)", re.MULTILINE | re.DOTALL)
MARKDOWN_LINK_RE = re.compile(r"(!?\[[^\]]*])\(([^)]+)\)")
RAW_URL_RE = re.compile(r"https?://[^\s)>\"]+")
OUTER_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*)\n```[ \t]*$", re.DOTALL)
FRONTMATTER_RE = re.compile(r"^(---\n.*?\n---\n)(.*)$", re.DOTALL)


@dataclass
class SourceFile:
    source_path: Path
    output_path: Path
    label: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate Markdown pages into Russian using OpenRouter."
    )
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--match",
        default=None,
        help="Only translate files whose relative source path contains this substring.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Translate even if the cached fingerprint matches the current source.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        help="Sleep between successful requests.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=4,
        help="Retries per file for transient model/API failures.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature for the translation model.",
    )
    parser.add_argument(
        "--skip-assets-sync",
        action="store_true",
        help="Do not mirror docs/pages/assets into docs/ru/pages/assets.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on the first translation error instead of continuing.",
    )
    parser.add_argument(
        "--only-failures",
        action="store_true",
        help="Translate only files listed in .translation-cache/openrouter-ru/failures.json.",
    )
    return parser.parse_args()


def ensure_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_PAGES_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"files": {}}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_source_files(match: str | None, limit: int | None) -> list[SourceFile]:
    files = [SourceFile(SOURCE_INDEX, OUTPUT_ROOT / "index.md", "index")]

    for path in sorted(SOURCE_PAGES_DIR.glob("*.md")):
        relative = path.relative_to(SOURCE_PAGES_DIR)
        files.append(SourceFile(path, OUTPUT_PAGES_DIR / relative, str(relative)))

    if match:
        lowered = match.lower()
        files = [item for item in files if lowered in item.label.lower()]

    if limit is not None:
        files = files[:limit]

    return files


def filter_only_failures(items: list[SourceFile]) -> list[SourceFile]:
    if not FAILURES_FILE.exists():
        return []
    failures = json.loads(FAILURES_FILE.read_text(encoding="utf-8"))
    failed_labels = {entry["file"] for entry in failures}
    return [item for item in items if item.label in failed_labels]


def sync_assets() -> None:
    if not SOURCE_ASSETS_DIR.exists():
        return
    if OUTPUT_ASSETS_DIR.exists():
        shutil.rmtree(OUTPUT_ASSETS_DIR)
    shutil.copytree(SOURCE_ASSETS_DIR, OUTPUT_ASSETS_DIR)


def load_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


def file_fingerprint(source_text: str, prompt_text: str, model: str) -> str:
    payload = "\n".join([PIPELINE_VERSION, model, prompt_text, source_text]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def split_frontmatter(markdown_text: str) -> tuple[str, str]:
    match = FRONTMATTER_RE.match(markdown_text)
    if not match:
        return "", markdown_text
    return match.group(1), match.group(2)


def translate_frontmatter(frontmatter: str, output_path: Path) -> str:
    if not frontmatter:
        return frontmatter

    text = frontmatter
    if output_path.name.startswith("page-"):
        page_number = output_path.stem.split("-")[-1]
        text = re.sub(
            r'^(title:\s*)"Page\s+(\d+)"\s*$',
            rf'\1"Страница {page_number}"',
            text,
            flags=re.MULTILINE,
        )
    elif output_path.name == "index.md" and output_path.parent.name == "pages":
        text = re.sub(r'^(title:\s*)"Pages"\s*$', r'\1"Страницы"', text, flags=re.MULTILINE)
    else:
        text = re.sub(r'^(title:\s*)"Pages"\s*$', r'\1"Страницы"', text, flags=re.MULTILINE)
    return text


def mask_segments(markdown_text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    counter = {"value": 0}

    def reserve(prefix: str, content: str) -> str:
        counter["value"] += 1
        token = f"⟪KEEP_{prefix}_{counter['value']:04d}⟫"
        placeholders[token] = content
        return token

    def replace_fenced(match: re.Match[str]) -> str:
        return reserve("CODEBLOCK", match.group(0))

    text = FENCED_CODE_RE.sub(replace_fenced, markdown_text)

    def replace_markdown_link(match: re.Match[str]) -> str:
        label = match.group(1)
        destination = match.group(2)
        token = reserve("LINKDEST", destination)
        return f"{label}({token})"

    text = MARKDOWN_LINK_RE.sub(replace_markdown_link, text)

    def replace_raw_url(match: re.Match[str]) -> str:
        return reserve("RAWURL", match.group(0))

    text = RAW_URL_RE.sub(replace_raw_url, text)
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


def normalize_translated_markdown(markdown_text: str) -> str:
    text = markdown_text
    text = re.sub(r"^(#\s+)Page\s+(\d+)\s*$", r"\1Страница \2", text, flags=re.MULTILINE)
    text = re.sub(r"^(#\s+)Pages\s*$", r"\1Страницы", text, flags=re.MULTILINE)
    text = re.sub(r"\[Pages index\]", "[Индекс страниц]", text)
    text = re.sub(r"\[<- Page (\d+)\]", r"[<- Страница \1]", text)
    text = re.sub(r"\[Page (\d+) ->\]", r"[Страница \1 ->]", text)
    text = re.sub(
        r"!\[Page (\d+) image (\d+)\]",
        r"![Страница \1, изображение \2]",
        text,
    )
    text = re.sub(r"- Total PDF pages:", "- Всего страниц в PDF:", text)
    text = re.sub(r"- First page:", "- Первая страница:", text)
    text = re.sub(r"- Last page:", "- Последняя страница:", text)
    text = re.sub(r"## Table of contents", "## Оглавление", text)
    return text


def extract_response_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts)
    raise RuntimeError("OpenRouter returned an unsupported content payload.")


def request_translation(
    api_key: str,
    model: str,
    prompt_text: str,
    rel_label: str,
    masked_markdown: str,
    temperature: float,
    max_retries: int,
) -> str:
    user_prompt = "\n".join(
        [
            "Переведи следующий markdown-файл книги на русский язык.",
            "",
            "Технические требования:",
            "- Верни только готовый markdown.",
            "- Сохраняй порядок блоков и структуру документа.",
            "- Не меняй токены вида ⟪KEEP_*⟫.",
            "- Не добавляй комментарии от себя.",
            "",
            f"Файл: {rel_label}",
            "",
            "Markdown для перевода:",
            masked_markdown,
        ]
    )

    body = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": user_prompt},
        ],
    }
    raw_body = json.dumps(body).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": APP_URL,
        "X-OpenRouter-Title": APP_TITLE,
    }

    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(
            OPENROUTER_URL, data=raw_body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return extract_response_content(payload)
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            should_retry = error.code in {408, 409, 429, 500, 502, 503, 504}
            if attempt >= max_retries or not should_retry:
                raise RuntimeError(
                    f"OpenRouter HTTP {error.code} for {rel_label}: {details}"
                ) from error
            time.sleep(min(2 ** attempt, 20))
        except urllib.error.URLError as error:
            if attempt >= max_retries:
                raise RuntimeError(f"Network error for {rel_label}: {error}") from error
            time.sleep(min(2 ** attempt, 20))

    raise RuntimeError(f"Failed to translate {rel_label} after {max_retries} attempts.")


def validate_placeholders(translated_text: str, placeholders: dict[str, str], rel_label: str) -> None:
    missing = [token for token in placeholders if token not in translated_text]
    if missing:
        preview = ", ".join(missing[:5])
        raise RuntimeError(
            f"Model damaged placeholders for {rel_label}. Missing {len(missing)} token(s): {preview}"
        )


def translate_one_file(
    item: SourceFile,
    api_key: str,
    model: str,
    prompt_text: str,
    temperature: float,
    max_retries: int,
) -> str:
    source_text = item.source_path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(source_text)
    masked_body, placeholders = mask_segments(body)
    translated_body = request_translation(
        api_key=api_key,
        model=model,
        prompt_text=prompt_text,
        rel_label=item.label,
        masked_markdown=masked_body,
        temperature=temperature,
        max_retries=max_retries,
    )
    translated_body = strip_outer_fence(translated_body)
    validate_placeholders(translated_body, placeholders, item.label)
    restored_body = restore_segments(translated_body, placeholders)
    restored_body = normalize_translated_markdown(restored_body)
    translated_frontmatter = translate_frontmatter(frontmatter, item.output_path)
    return translated_frontmatter + restored_body


def write_output(output_path: Path, content: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit(
            "Missing OpenRouter API key. Set OPENROUTER_API_KEY or pass --api-key explicitly."
        )

    ensure_dirs()
    if not args.skip_assets_sync:
        sync_assets()

    prompt_text = load_prompt()
    state = load_state()
    state.setdefault("files", {})

    source_files = collect_source_files(args.match, args.limit)
    if args.only_failures:
        source_files = filter_only_failures(source_files)
    if not source_files:
        print("No source files matched the requested filters.")
        return

    translated = 0
    skipped = 0
    failed = 0
    failures: list[dict[str, str]] = []

    for item in source_files:
        source_text = item.source_path.read_text(encoding="utf-8")
        fingerprint = file_fingerprint(source_text, prompt_text, args.model)
        state_entry = state["files"].get(item.label)

        if (
            not args.overwrite
            and state_entry
            and state_entry.get("fingerprint") == fingerprint
            and item.output_path.exists()
        ):
            print(f"skip {item.label}")
            skipped += 1
            continue

        print(f"translate {item.label}")
        try:
            translated_content = translate_one_file(
                item=item,
                api_key=args.api_key,
                model=args.model,
                prompt_text=prompt_text,
                temperature=args.temperature,
                max_retries=args.max_retries,
            )
            write_output(item.output_path, translated_content)

            state["files"][item.label] = {
                "fingerprint": fingerprint,
                "model": args.model,
                "source": str(item.source_path.relative_to(ROOT)).replace("\\", "/"),
                "output": str(item.output_path.relative_to(ROOT)).replace("\\", "/"),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            save_state(state)
            translated += 1
        except Exception as error:
            failed += 1
            failures.append({"file": item.label, "error": str(error)})
            print(f"error {item.label}: {error}", file=sys.stderr)
            if args.fail_fast:
                raise

        if args.delay_seconds > 0:
            time.sleep(args.delay_seconds)

    if failures:
        FAILURES_FILE.write_text(
            json.dumps(failures, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif FAILURES_FILE.exists():
        FAILURES_FILE.unlink()

    print(
        f"done translated={translated} skipped={skipped} failed={failed} output={OUTPUT_ROOT}"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
