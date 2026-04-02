#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "Functional_Programming_in_Scala_Second_.pdf"
DOCS_DIR = ROOT / "docs"
PAGES_DIR = DOCS_DIR / "pages"
ASSETS_DIR = PAGES_DIR / "assets"
LEGACY_ASSETS_DIR = DOCS_DIR / "public" / "page-assets"
VITEPRESS_DIR = DOCS_DIR / ".vitepress"
SIDEBAR_FILE = VITEPRESS_DIR / "sidebar.mjs"
PAGES_INDEX_FILE = PAGES_DIR / "index.md"

BOOK_TITLE = "Functional Programming in Scala, Second Edition"
MONO_FONT_HINTS = ("Courier", "Mono")
INLINE_CODE_HINTS = ("Courier",)
DECORATIVE_IMAGE_MIN_WIDTH = 1000
DECORATIVE_IMAGE_MAX_HEIGHT = 100
LICENSE_MARKER = "Licensed to "
VECTOR_IMAGE_SCALE = 2.0
VECTOR_MIN_AREA = 5000
VECTOR_MIN_WIDTH = 60
VECTOR_MIN_HEIGHT = 40
VECTOR_MERGE_GAP = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert the source PDF into one Markdown file per page for VitePress."
    )
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="Path to the source PDF.")
    parser.add_argument(
        "--page-start", type=int, default=1, help="First 1-based page to export."
    )
    parser.add_argument(
        "--page-end", type=int, default=None, help="Last 1-based page to export."
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not clear generated pages/assets before writing new output.",
    )
    return parser.parse_args()


def ensure_dirs() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    VITEPRESS_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def clean_generated_output() -> None:
    if PAGES_DIR.exists():
        shutil.rmtree(PAGES_DIR)
    if LEGACY_ASSETS_DIR.exists():
        shutil.rmtree(LEGACY_ASSETS_DIR)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def is_mono_font(font_name: str) -> bool:
    return any(hint in font_name for hint in MONO_FONT_HINTS)


def is_inline_code_font(font_name: str) -> bool:
    return any(hint in font_name for hint in INLINE_CODE_HINTS)


def normalize_text(text: str) -> str:
    return (
        text.replace("\u00a0", " ")
        .replace("\u2009", " ")
        .replace("\u202f", " ")
        .replace("\r", "")
    )


def strip_md_wrapping(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def escape_inline_code(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if "`" in text:
        return f"``{text}``"
    return f"`{text}`"


def render_span(span: dict) -> str:
    text = normalize_text(span.get("text", ""))
    if not text:
        return ""
    font_name = span.get("font", "")
    flags = span.get("flags", 0)
    is_mono = is_inline_code_font(font_name)
    is_bold = bool(flags & 16) or "Bold" in font_name or "Black" in font_name
    is_italic = bool(flags & 2) or "Italic" in font_name or "Ital" in font_name

    if is_mono:
        return escape_inline_code(text)

    rendered = text
    if is_bold and is_italic:
        rendered = f"***{rendered}***"
    elif is_bold:
        rendered = f"**{rendered}**"
    elif is_italic:
        rendered = f"*{rendered}*"
    return rendered


def collapse_rendered_lines(raw_lines: list[str], rendered_lines: list[str]) -> str:
    if not rendered_lines:
        return ""

    output = rendered_lines[0].rstrip()
    previous_raw = raw_lines[0].rstrip()

    for raw, rendered in zip(raw_lines[1:], rendered_lines[1:]):
        raw = raw.rstrip()
        rendered = rendered.rstrip()
        if not rendered:
            previous_raw = raw
            continue

        if previous_raw.endswith("-") and raw[:1].islower():
            output = output[:-1] + rendered.lstrip()
        elif output.endswith(("/", "(", "[", "{")):
            output += rendered.lstrip()
        else:
            output += " " + rendered.lstrip()

        previous_raw = raw

    output = re.sub(r"\s+", " ", output)
    output = output.replace(" ,", ",").replace(" .", ".").replace(" :", ":")
    return output.strip()


def block_plain_lines(block: dict) -> list[str]:
    lines: list[str] = []
    for line in block.get("lines", []):
        text = "".join(normalize_text(span.get("text", "")) for span in line.get("spans", []))
        text = text.rstrip()
        if text:
            lines.append(text)
    return lines


def block_rendered_lines(block: dict) -> list[str]:
    lines: list[str] = []
    for line in block.get("lines", []):
        rendered = "".join(render_span(span) for span in line.get("spans", []))
        rendered = rendered.rstrip()
        if rendered:
            lines.append(rendered)
    return lines


def block_font_stats(block: dict) -> tuple[Counter, list[float], int, int]:
    fonts: Counter = Counter()
    sizes: list[float] = []
    mono_chars = 0
    total_chars = 0
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = normalize_text(span.get("text", ""))
            if not text:
                continue
            font_name = span.get("font", "")
            fonts[font_name] += len(text)
            sizes.append(float(span.get("size", 0)))
            total_chars += len(text)
            if is_mono_font(font_name):
                mono_chars += len(text)
    return fonts, sizes, mono_chars, total_chars


def is_running_header(block: dict, text: str) -> bool:
    y0 = block.get("bbox", [0, 0, 0, 0])[1]
    normalized = strip_md_wrapping(text)
    if y0 >= 45:
        return False
    if re.fullmatch(r"\d+", normalized):
        return True
    return "CHAPTER " in normalized.upper()


def is_footer(text: str) -> bool:
    return LICENSE_MARKER in text


def heading_level_for_block(
    fonts: Counter, max_size: float, y0: float, plain_text: str
) -> int | None:
    font_names = list(fonts)
    if any("Univers-Black" in name for name in font_names):
        return 4
    if any("FranklinGothic-DemiItal" in name for name in font_names):
        return 3
    if any("NewBaskerville-BoldItali" in name for name in font_names):
        return 2 if y0 < 120 else 3
    if max_size >= 14 and len(strip_md_wrapping(plain_text).split()) >= 3:
        return 2
    return None


def classify_text_block(block: dict) -> tuple[str, str] | None:
    raw_lines = block_plain_lines(block)
    if not raw_lines:
        return None

    plain_text = "\n".join(raw_lines)
    if is_footer(plain_text) or is_running_header(block, plain_text):
        return None

    rendered_lines = block_rendered_lines(block)
    fonts, sizes, mono_chars, total_chars = block_font_stats(block)
    y0 = block.get("bbox", [0, 0, 0, 0])[1]
    max_size = max(sizes) if sizes else 0
    mono_ratio = (mono_chars / total_chars) if total_chars else 0

    if mono_ratio >= 0.9:
        code = "\n".join(line.rstrip() for line in raw_lines).strip()
        if not code:
            return None
        return ("code", code)

    heading_level = heading_level_for_block(fonts, max_size, y0, plain_text)
    if heading_level is not None:
        heading_text = collapse_rendered_lines(raw_lines, raw_lines)
        heading_text = heading_text.lstrip("# ").strip()
        if heading_text:
            return ("heading", f"{'#' * heading_level} {heading_text}")

    if any("Humanist521BT-BoldConden" in name for name in fonts):
        note_text = collapse_rendered_lines(raw_lines, raw_lines)
        if note_text:
            return ("note", f"> {note_text}")
        return None

    paragraph = collapse_rendered_lines(raw_lines, rendered_lines)
    if not paragraph:
        return None

    if all(line.lstrip().startswith(("•", "-", "*")) for line in raw_lines):
        bullet_lines = []
        for line in rendered_lines:
            cleaned = re.sub(r"^[•*-]\s*", "", line.lstrip())
            bullet_lines.append(f"- {cleaned}")
        return ("list", "\n".join(bullet_lines))

    return ("paragraph", paragraph)


def is_decorative_image(block: dict) -> bool:
    width = int(block.get("width", 0))
    height = int(block.get("height", 0))
    return width >= DECORATIVE_IMAGE_MIN_WIDTH and height <= DECORATIVE_IMAGE_MAX_HEIGHT


def save_image_block(block: dict, page_dir: Path, page_number: int, image_index: int) -> str | None:
    if is_decorative_image(block):
        return None

    image_bytes = block.get("image")
    if not image_bytes:
        return None

    ext = block.get("ext", "png")
    page_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"image-{image_index:02d}.{ext}"
    file_path = page_dir / file_name
    file_path.write_bytes(image_bytes)
    return f"./assets/page-{page_number:04d}/{file_name}"


def short_text_rects(blocks: list[dict]) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        lines = block.get("lines", [])
        text = "".join(
            "".join(normalize_text(span.get("text", "")) for span in line.get("spans", []))
            for line in lines
        ).strip()
        if not text:
            continue
        if len(text) <= 90 or len(lines) <= 2:
            rects.append(fitz.Rect(block["bbox"]))
    return rects


def rects_are_near(a: fitz.Rect, b: fitz.Rect, gap: float = VECTOR_MERGE_GAP) -> bool:
    return not (
        a.x1 < b.x0 - gap
        or a.x0 > b.x1 + gap
        or a.y1 < b.y0 - gap
        or a.y0 > b.y1 + gap
    )


def overlaps_strongly(a: fitz.Rect, b: fitz.Rect) -> bool:
    intersection = a & b
    if intersection.is_empty:
        return False
    return intersection.get_area() / min(a.get_area(), b.get_area()) > 0.85


def vector_regions(page: fitz.Page, blocks: list[dict]) -> list[fitz.Rect]:
    drawing_rects: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue
        if rect.width >= 300 and rect.height <= 24:
            continue
        if rect.width < 6 and rect.height < 6:
            continue
        drawing_rects.append(fitz.Rect(rect.x0 - 12, rect.y0 - 12, rect.x1 + 12, rect.y1 + 12))

    if not drawing_rects:
        return []

    clusters: list[fitz.Rect] = []
    for rect in sorted(drawing_rects, key=lambda item: (item.y0, item.x0)):
        merged = False
        for index, cluster in enumerate(clusters):
            if rects_are_near(rect, cluster):
                clusters[index] = cluster | rect
                merged = True
                break
        if not merged:
            clusters.append(rect)

    changed = True
    while changed:
        changed = False
        reduced: list[fitz.Rect] = []
        pending = clusters[:]
        while pending:
            cluster = pending.pop(0)
            cursor = 0
            while cursor < len(pending):
                other = pending[cursor]
                if rects_are_near(cluster, other):
                    cluster = cluster | other
                    pending.pop(cursor)
                    changed = True
                else:
                    cursor += 1
            reduced.append(cluster)
        clusters = reduced

    label_rects = short_text_rects(blocks)
    enriched: list[fitz.Rect] = []
    for cluster in clusters:
        current = cluster
        for rect in label_rects:
            if rects_are_near(current, rect, gap=10):
                current = current | rect
        current = current & page.rect
        if (
            current.width >= VECTOR_MIN_WIDTH
            and current.height >= VECTOR_MIN_HEIGHT
            and current.get_area() >= VECTOR_MIN_AREA
        ):
            enriched.append(current)

    deduped: list[fitz.Rect] = []
    for rect in sorted(enriched, key=lambda item: item.get_area(), reverse=True):
        if any(overlaps_strongly(rect, existing) for existing in deduped):
            continue
        deduped.append(rect)

    return sorted(deduped, key=lambda item: (item.y0, item.x0))


def save_vector_region(
    page: fitz.Page, rect: fitz.Rect, page_dir: Path, page_number: int, image_index: int
) -> str:
    page_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"image-{image_index:02d}.png"
    file_path = page_dir / file_name
    matrix = fitz.Matrix(VECTOR_IMAGE_SCALE, VECTOR_IMAGE_SCALE)
    pixmap = page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
    pixmap.save(file_path)
    return f"./assets/page-{page_number:04d}/{file_name}"


def context_titles_for_page(page_number: int, toc: list[list[int | str]]) -> list[str]:
    active: dict[int, str] = {}
    for level, title, toc_page in toc:
        if toc_page > page_number:
            break
        active[level] = str(title)
        active = {lvl: value for lvl, value in active.items() if lvl <= level}
    return [active[level] for level in sorted(active)]


def page_nav_block(page_number: int, page_count: int) -> str:
    links: list[str] = []
    if page_number > 1:
        links.append(f"[<- Page {page_number - 1:04d}](./page-{page_number - 1:04d})")
    links.append("[Pages index](./)")
    if page_number < page_count:
        links.append(f"[Page {page_number + 1:04d} ->](./page-{page_number + 1:04d})")
    return " | ".join(links)


def build_page_markdown(
    page: fitz.Page,
    page_number: int,
    page_count: int,
    toc: list[list[int | str]],
) -> str:
    blocks = page.get_text("dict", sort=True).get("blocks", [])
    visuals = [
        {"type": "vector", "bbox": tuple(region), "rect": region}
        for region in vector_regions(page, blocks)
    ]
    events = list(blocks) + visuals
    events.sort(key=lambda item: (item.get("bbox", [0, 0, 0, 0])[1], item.get("bbox", [0, 0, 0, 0])[0]))
    parts: list[str] = []
    image_index = 1
    page_asset_dir = ASSETS_DIR / f"page-{page_number:04d}"

    parts.append("---")
    parts.append(f'title: "Page {page_number:04d}"')
    parts.append("outline: false")
    parts.append("---")
    parts.append("")
    parts.append(f"# Page {page_number:04d}")
    parts.append("")
    parts.append(page_nav_block(page_number, page_count))

    context_titles = context_titles_for_page(page_number, toc)
    if context_titles:
        parts.append("")
        parts.append("> " + " / ".join(context_titles))

    parts.append("")

    pending_code: list[str] = []

    def flush_code() -> None:
        if not pending_code:
            return
        code = "\n".join(pending_code).rstrip()
        if code:
            parts.append("```scala")
            parts.append(code)
            parts.append("```")
            parts.append("")
        pending_code.clear()

    for block in events:
        block_type = block.get("type")
        if block_type == 1:
            flush_code()
            image_url = save_image_block(block, page_asset_dir, page_number, image_index)
            if image_url:
                parts.append(f"![Page {page_number:04d} image {image_index}]({image_url})")
                parts.append("")
                image_index += 1
            continue
        if block_type == "vector":
            flush_code()
            image_url = save_vector_region(page, block["rect"], page_asset_dir, page_number, image_index)
            parts.append(f"![Page {page_number:04d} image {image_index}]({image_url})")
            parts.append("")
            image_index += 1
            continue

        if block_type != 0:
            continue

        classified = classify_text_block(block)
        if not classified:
            continue

        kind, content = classified
        if kind == "code":
            pending_code.append(content)
            continue

        flush_code()
        parts.append(content)
        parts.append("")

    flush_code()
    parts.append(page_nav_block(page_number, page_count))
    parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def top_level_toc_items(toc: list[list[int | str]]) -> list[dict[str, int | str]]:
    items = []
    for level, title, page in toc:
        if level == 1:
            items.append({"title": str(title), "page": int(page)})
    return items


def write_pages_index(page_count: int, toc: list[list[int | str]]) -> None:
    top_items = top_level_toc_items(toc)
    lines = [
        "---",
        'title: "Pages"',
        "---",
        "",
        "# Pages",
        "",
        f"- Total PDF pages: **{page_count}**",
        f"- First page: [Page 0001](./page-0001)",
        f"- Last page: [Page {page_count:04d}](./page-{page_count:04d})",
        "",
        "## Table of contents",
        "",
    ]

    for item in top_items:
        title = str(item["title"]).replace("[", "\\[").replace("]", "\\]")
        page = int(item["page"])
        lines.append(f"- [{title} (page {page})](./page-{page:04d})")

    PAGES_INDEX_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_sidebar(toc: list[list[int | str]]) -> None:
    items = [{"text": "Pages overview", "link": "/pages/"}]
    for item in top_level_toc_items(toc):
        text = json.dumps(f'{item["title"]} (p. {item["page"]})', ensure_ascii=False)
        link = json.dumps(f'/pages/page-{int(item["page"]):04d}')
        items.append(f"      {{ text: {text}, link: {link} }}")

    sidebar_content = [
        "export default [",
        "  {",
        "    text: 'Book',",
        "    items: [",
        "      { text: 'Pages overview', link: '/pages/' },",
    ]

    for item in top_level_toc_items(toc):
        text = json.dumps(f'{item["title"]} (p. {item["page"]})', ensure_ascii=False)
        link = json.dumps(f'/pages/page-{int(item["page"]):04d}')
        sidebar_content.append(f"      {{ text: {text}, link: {link} }},")

    sidebar_content.extend(
        [
            "    ]",
            "  }",
            "]",
        ]
    )
    SIDEBAR_FILE.write_text("\n".join(sidebar_content) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    ensure_dirs()
    if not args.no_clean:
        clean_generated_output()

    document = fitz.open(pdf_path)
    toc = document.get_toc()
    page_count = document.page_count
    page_start = max(1, args.page_start)
    page_end = min(page_count, args.page_end or page_count)

    for page_number in range(page_start, page_end + 1):
        page = document.load_page(page_number - 1)
        markdown = build_page_markdown(page, page_number, page_count, toc)
        output_path = PAGES_DIR / f"page-{page_number:04d}.md"
        output_path.write_text(markdown, encoding="utf-8")

    write_pages_index(page_count, toc)
    write_sidebar(toc)

    print(
        f"Converted pages {page_start}-{page_end} of {page_count} from "
        f"{pdf_path.name} into {PAGES_DIR}"
    )


if __name__ == "__main__":
    main()
