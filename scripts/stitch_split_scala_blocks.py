#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RU_PAGES_DIR = ROOT / "docs" / "ru" / "pages"


@dataclass(frozen=True)
class Repair:
    left_page: str
    left_block: int
    right_page: str
    right_block: int
    combined_block: str


REPAIRS = [
    Repair(
        left_page="page-0369.md",
        left_block=6,
        right_page="page-0370.md",
        right_block=1,
        combined_block="""```scala
extension [A](fa: F[A])
  def map3[B, C, D](
    fb: F[B],
    fc: F[C]
  )(f: (A, B, C) => D): F[D] =
    apply(apply(apply(unit(f.curried))(fa))(fb))(fc)

  def map4[B, C, D, E](
    fb: F[B],
    fc: F[C],
    fd: F[D]
  )(f: (A, B, C, D) => E): F[E] =
    apply(apply(apply(apply(unit(f.curried))(fa))(fb))(fc))(fd)
```""",
    ),
    Repair(
        left_page="page-0372.md",
        left_block=12,
        right_page="page-0373.md",
        right_block=1,
        combined_block="""```scala
fa.flatMap(a =>
  fb.flatMap(b =>
    fc.map(c => (b, c)))
    .map(bc => (a, bc)))
  .map(assoc)
```""",
    ),
    Repair(
        left_page="page-0473.md",
        left_block=1,
        right_page="page-0474.md",
        right_block=1,
        combined_block="""```scala
def fromListViaUnfold[O](os: List[O]): Pull[O, Unit] =
  unfold(os):
    case Nil => Left(Nil)
    case hd :: tl => Right((hd, tl))
  .map(_ => ())

def fromLazyListViaUnfold[O](os: LazyList[O]): Pull[O, Unit] =
  unfold(os):
    case LazyList() => Left(LazyList())
    case hd #:: tl => Right((hd, tl))
  .map(_ => ())
```""",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair known split Scala code blocks across adjacent pages.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def split_parts(markdown_text: str) -> list[tuple[str, str]]:
    lines = markdown_text.splitlines(keepends=True)
    parts: list[tuple[str, str]] = []
    text_chunk: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            if text_chunk:
                parts.append(("text", "".join(text_chunk)))
                text_chunk = []

            info = line[3:].strip()
            block_lines = [line]
            index += 1
            while index < len(lines):
                block_lines.append(lines[index])
                if lines[index].startswith("```"):
                    index += 1
                    break
                index += 1
            kind = "scala" if info.startswith("scala") else "fence"
            parts.append((kind, "".join(block_lines)))
            continue

        text_chunk.append(line)
        index += 1

    if text_chunk:
        parts.append(("text", "".join(text_chunk)))
    return parts


def replace_scala_block(markdown_text: str, block_number: int, replacement: str) -> str:
    parts = split_parts(markdown_text)
    current = 0
    out: list[str] = []
    for kind, content in parts:
        if kind == "scala":
            current += 1
            if current == block_number:
                out.append(replacement.rstrip() + "\n")
            else:
                out.append(content)
        else:
            out.append(content)
    if current < block_number:
        raise RuntimeError(f"Scala block #{block_number} not found.")
    return "".join(out)


def apply_repair(repair: Repair, overwrite: bool) -> tuple[bool, str]:
    left_path = RU_PAGES_DIR / repair.left_page
    right_path = RU_PAGES_DIR / repair.right_page
    left_text = left_path.read_text(encoding="utf-8")
    right_text = right_path.read_text(encoding="utf-8")

    new_left = replace_scala_block(left_text, repair.left_block, repair.combined_block)
    new_right = replace_scala_block(right_text, repair.right_block, repair.combined_block)
    changed = (new_left != left_text) or (new_right != right_text)

    if overwrite and changed:
        left_path.write_text(new_left, encoding="utf-8")
        right_path.write_text(new_right, encoding="utf-8")

    return changed, f"{repair.left_page}#{repair.left_block} <-> {repair.right_page}#{repair.right_block}"


def main() -> None:
    args = parse_args()
    for repair in REPAIRS:
        changed, label = apply_repair(repair, overwrite=args.overwrite)
        status = "patched" if changed else "already-ok"
        print(f"{status} {label}")


if __name__ == "__main__":
    main()
