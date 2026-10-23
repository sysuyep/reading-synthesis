#!/usr/bin/env python3
"""Build a compact Markdown context pack around aligned WeRead highlights."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def quote_markdown(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in str(text).splitlines())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paragraphs", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--before", type=int, default=2)
    parser.add_argument("--after", type=int, default=3)
    parser.add_argument("--max-chars", type=int, default=7000, help="Maximum context characters per merged block")
    parser.add_argument("--ids", help="Comma-separated bookmark IDs; default is all matched highlights")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    paragraphs = sorted(read_jsonl(args.paragraphs), key=lambda item: int(item["seq"]))
    report = json.loads(args.alignment.read_text(encoding="utf-8"))
    selected_ids = {item.strip() for item in args.ids.split(",")} if args.ids else None
    positions = {int(item["seq"]): index for index, item in enumerate(paragraphs)}
    candidates = []
    unmatched = []
    for item in report.get("alignments", []):
        bookmark_id = str(item.get("bookmark_id", ""))
        if selected_ids is not None and bookmark_id not in selected_ids:
            continue
        if item.get("status") == "unmatched" or item.get("seq_start") is None:
            unmatched.append(item)
            continue
        start_index = positions.get(int(item["seq_start"]))
        end_index = positions.get(int(item["seq_end"]))
        if start_index is None or end_index is None:
            unmatched.append(item)
            continue
        chapter = paragraphs[start_index].get("chapter", "")
        left = start_index
        steps = max(0, args.before)
        while left > 0 and steps and paragraphs[left - 1].get("chapter", "") == chapter:
            left -= 1
            steps -= 1
        right = end_index
        steps = max(0, args.after)
        while right + 1 < len(paragraphs) and steps and paragraphs[right + 1].get("chapter", "") == chapter:
            right += 1
            steps -= 1
        candidates.append(
            {
                "chapter": chapter,
                "left": left,
                "right": right,
                "highlights": [item],
            }
        )

    merged: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda value: (value["chapter"], value["left"], value["right"])):
        if merged and merged[-1]["chapter"] == item["chapter"] and item["left"] <= merged[-1]["right"] + 1:
            merged[-1]["right"] = max(merged[-1]["right"], item["right"])
            merged[-1]["highlights"].extend(item["highlights"])
        else:
            merged.append(item)

    lines = [
        "# 电子书上下文包",
        "",
        "> 仅包含与微信读书划线命中的局部段落。引用为微信读书逐字原文，电子书上下文来自已对齐版本。",
        "",
    ]
    chapter_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chapter_order: list[str] = []
    for item in merged:
        if item["chapter"] not in chapter_groups:
            chapter_order.append(item["chapter"])
        chapter_groups[item["chapter"]].append(item)
    for chapter in chapter_order:
        lines.extend([f"## {chapter or '未命名章节'}", ""])
        for block_number, block in enumerate(chapter_groups[chapter], 1):
            lines.extend([f"### 上下文 {block_number}", "", "**对应划线**", ""])
            seen: set[str] = set()
            for highlight in block["highlights"]:
                bookmark_id = str(highlight.get("bookmark_id", ""))
                if bookmark_id in seen:
                    continue
                seen.add(bookmark_id)
                lines.append(f"`{bookmark_id}` · {highlight.get('status')} · {highlight.get('score')}")
                lines.append(quote_markdown(str(highlight.get("text", ""))))
                lines.append("")
            lines.extend(["**电子书上下文**", ""])
            used = 0
            for paragraph in paragraphs[block["left"] : block["right"] + 1]:
                text = str(paragraph.get("text", ""))
                if used + len(text) > max(200, args.max_chars):
                    lines.append("_[上下文达到字符上限，后续段落已省略]_\n")
                    break
                location = f"段落 {paragraph['seq']}"
                if paragraph.get("page"):
                    location += f" · PDF 第 {paragraph['page']} 页"
                lines.extend([f"**{location}**", text, ""])
                used += len(text)
    if unmatched:
        lines.extend(["## 未匹配划线", ""])
        for item in unmatched:
            lines.extend([f"`{item.get('bookmark_id')}`", quote_markdown(str(item.get("text", ""))), ""])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(args.out.resolve()),
                "matched_highlights": sum(len(item["highlights"]) for item in merged),
                "merged_context_blocks": len(merged),
                "unmatched_highlights": len(unmatched),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

