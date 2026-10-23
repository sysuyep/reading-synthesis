#!/usr/bin/env python3
"""Diff complete WeRead notes, update machine state, and render a folded evidence index."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def die(message: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die(f"Cannot read JSON file {path}: {exc}")
    if not isinstance(value, dict):
        die(f"Expected a JSON object in {path}")
    return value


def stable_fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def unwrap_reviews(payload: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = payload.get("reviews", [])
    if not isinstance(reviews, list):
        die("reviews JSON must contain a reviews array")
    if payload.get("hasMore"):
        die("reviews JSON is incomplete (hasMore=1); fetch every page before syncing")
    total = payload.get("totalCount")
    if isinstance(total, int) and total > len(reviews):
        die(f"reviews JSON is incomplete ({len(reviews)}/{total}); fetch every page before syncing")
    normalized: list[dict[str, Any]] = []
    for item in reviews:
        if not isinstance(item, dict):
            continue
        review = item.get("review", item)
        if isinstance(review, dict) and review.get("reviewId"):
            normalized.append(review)
    return normalized


def quote_lines(value: str, prefix: str) -> list[str]:
    lines = str(value).splitlines() or [""]
    return [f"{prefix}{line}" if line else prefix.rstrip() for line in lines]


def range_start(value: Any) -> int:
    try:
        return int(str(value or "").split("-", 1)[0])
    except ValueError:
        return 10**18


def render_markdown(
    chapters: list[dict[str, Any]],
    highlights: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    highlight_keys: dict[tuple[str, str], str],
) -> str:
    ordered_chapters = sorted(
        chapters,
        key=lambda item: (range_start(item.get("chapterIdx")), str(item.get("title", ""))),
    )
    chapter_titles = {str(item.get("chapterUid")): str(item.get("title", "")) for item in ordered_chapters}
    chapter_order = [str(item.get("chapterUid")) for item in ordered_chapters]
    grouped_highlights: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_reviews: dict[str, list[dict[str, Any]]] = defaultdict(list)
    linked_reviews: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in highlights:
        grouped_highlights[str(item.get("chapterUid", ""))].append(item)
    for review in reviews:
        chapter_uid = str(review.get("chapterUid", ""))
        linked = highlight_keys.get((chapter_uid, str(review.get("range", ""))))
        if linked:
            linked_reviews[linked].append(review)
        else:
            grouped_reviews[chapter_uid].append(review)
    for items in grouped_highlights.values():
        items.sort(key=lambda item: (range_start(item.get("range")), int(item.get("createTime", 0) or 0)))
    for items in grouped_reviews.values():
        items.sort(key=lambda item: (range_start(item.get("range")), int(item.get("createTime", 0) or 0)))
    for chapter_uid in list(grouped_highlights) + list(grouped_reviews):
        if chapter_uid not in chapter_order:
            chapter_order.append(chapter_uid)

    lines = ["## 原文索引", "", "> [!info] 说明", "> 按章节保存微信读书逐字原文与个人想法；颜色、位置、时间和同步 ID 不在阅读视图中展示。", ""]
    for chapter_uid in chapter_order:
        chapter_highlights = grouped_highlights.get(chapter_uid, [])
        chapter_reviews = grouped_reviews.get(chapter_uid, [])
        if not chapter_highlights and not chapter_reviews:
            continue
        title = chapter_titles.get(chapter_uid) or next(
            (str(item.get("chapterName", "")) for item in chapter_reviews if item.get("chapterName")),
            "全书或未定位内容",
        )
        item_count = (
            len(chapter_highlights)
            + len(chapter_reviews)
            + sum(len(linked_reviews.get(str(item.get("bookmarkId", "")), [])) for item in chapter_highlights)
        )
        lines.extend([f"> [!note]- {title}（{item_count} 条）", ">"])
        for index, highlight in enumerate(chapter_highlights):
            lines.extend(quote_lines(str(highlight.get("markText", "")), "> > "))
            bookmark_id = str(highlight.get("bookmarkId", ""))
            for review in linked_reviews.get(bookmark_id, []):
                lines.extend([">", "> **我的想法：**"])
                lines.extend(quote_lines(str(review.get("content", "")), "> "))
            if index < len(chapter_highlights) - 1 or chapter_reviews:
                lines.extend([">", "> ---", ">"])
        for index, review in enumerate(chapter_reviews):
            abstract = str(review.get("abstract", "")).strip()
            if abstract:
                lines.extend(quote_lines(abstract, "> > "))
                lines.append(">")
            lines.append("> **我的想法：**")
            lines.extend(quote_lines(str(review.get("content", "")), "> "))
            if index < len(chapter_reviews) - 1:
                lines.extend([">", "> ---", ">"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bookmarks", type=Path, required=True, help="Complete /book/bookmarklist response")
    parser.add_argument("--reviews", type=Path, required=True, help="Complete, aggregated /review/list/mine response")
    parser.add_argument("--state", type=Path, required=True, help="Persistent machine-state JSON")
    parser.add_argument("--changes", type=Path, required=True, help="Diff report for the current sync")
    parser.add_argument("--markdown-out", type=Path, required=True, help="Folded exact-source index")
    args = parser.parse_args()

    bookmarks = load_json(args.bookmarks)
    review_payload = load_json(args.reviews)
    highlights = [item for item in bookmarks.get("updated", []) if isinstance(item, dict) and item.get("bookmarkId")]
    reviews = unwrap_reviews(review_payload)
    chapters = [item for item in bookmarks.get("chapters", []) if isinstance(item, dict)]
    chapter_titles = {str(item.get("chapterUid")): str(item.get("title", "")) for item in chapters}
    highlight_keys = {
        (str(item.get("chapterUid", "")), str(item.get("range", ""))): str(item.get("bookmarkId"))
        for item in highlights
    }

    evidence: dict[str, dict[str, Any]] = {}
    for item in highlights:
        bookmark_id = str(item["bookmarkId"])
        value = {
            "kind": "highlight",
            "source_id": bookmark_id,
            "chapter_uid": item.get("chapterUid"),
            "chapter": chapter_titles.get(str(item.get("chapterUid", "")), ""),
            "text": str(item.get("markText", "")),
            "range": item.get("range"),
            "create_time": item.get("createTime"),
        }
        value["fingerprint"] = stable_fingerprint(value)
        evidence[f"highlight:{bookmark_id}"] = value
    for item in reviews:
        review_id = str(item["reviewId"])
        chapter_uid = str(item.get("chapterUid", ""))
        value = {
            "kind": "thought",
            "source_id": review_id,
            "chapter_uid": item.get("chapterUid"),
            "chapter": chapter_titles.get(chapter_uid) or str(item.get("chapterName", "")),
            "abstract": str(item.get("abstract", "")),
            "content": str(item.get("content", "")),
            "range": item.get("range"),
            "linked_bookmark_id": highlight_keys.get((chapter_uid, str(item.get("range", "")))),
            "create_time": item.get("createTime"),
        }
        value["fingerprint"] = stable_fingerprint(value)
        evidence[f"thought:{review_id}"] = value

    previous = load_json(args.state) if args.state.is_file() else {}
    previous_evidence = previous.get("evidence", {}) if isinstance(previous.get("evidence", {}), dict) else {}
    added = sorted(set(evidence) - set(previous_evidence))
    deleted = sorted(set(previous_evidence) - set(evidence))
    modified = sorted(
        key
        for key in set(evidence) & set(previous_evidence)
        if evidence[key].get("fingerprint") != previous_evidence[key].get("fingerprint")
    )
    changed_chapters = sorted(
        {
            str(record.get("chapter", ""))
            for key in added + modified + deleted
            for record in [evidence.get(key) or previous_evidence.get(key) or {}]
            if record.get("chapter")
        }
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    book = bookmarks.get("book", {}) if isinstance(bookmarks.get("book", {}), dict) else {}
    state = {
        "schema_version": 1,
        "updated_at": timestamp,
        "book": book,
        "evidence": evidence,
    }
    changes = {
        "schema_version": 1,
        "synced_at": timestamp,
        "book_id": book.get("bookId") or (highlights[0].get("bookId") if highlights else None),
        "summary": {
            "current_evidence": len(evidence),
            "added": len(added),
            "modified": len(modified),
            "deleted": len(deleted),
            "changed_chapters": changed_chapters,
        },
        "added": added,
        "modified": modified,
        "deleted": deleted,
    }

    for path in (args.state, args.changes, args.markdown_out):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.changes.write_text(json.dumps(changes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_out.write_text(
        render_markdown(chapters, highlights, reviews, highlight_keys),
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, **changes["summary"], "state": str(args.state.resolve())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
