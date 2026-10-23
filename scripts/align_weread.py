#!/usr/bin/env python3
"""Align WeRead highlights to extracted ebook paragraphs and report edition compatibility."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


PUNCTUATION_RE = re.compile(r"[\s\u3000，。！？；：、,.!?;:'\"“”‘’（）()【】\[\]《》〈〉—…·\-]+")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = re.sub(r"[\[【](?:插图|图片|图)[\]】]", "", value)
    value = re.sub(r"[\[【]\d{1,4}[\]】]", "", value)
    return PUNCTUATION_RE.sub("", value)


def similarity(left: str, right: str) -> float:
    left_n, right_n = normalize(left), normalize(right)
    if not left_n or not right_n:
        return 0.0
    if left_n in right_n or right_n in left_n:
        return min(len(left_n), len(right_n)) / max(len(left_n), len(right_n))
    return SequenceMatcher(None, left_n, right_n, autojunk=False).ratio()


def grams(value: str, width: int = 3) -> set[str]:
    if len(value) <= width:
        return {value} if value else set()
    return {value[index : index + width] for index in range(len(value) - width + 1)}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return sorted(records, key=lambda item: int(item["seq"]))


def make_windows(paragraphs: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chapter_order: list[str] = []
    for item in paragraphs:
        chapter = item.get("chapter", "")
        if chapter not in by_chapter:
            chapter_order.append(chapter)
        by_chapter[chapter].append(item)
    windows: list[dict[str, Any]] = []
    for chapter in chapter_order:
        items = by_chapter[chapter]
        for start in range(len(items)):
            selected = items[start : start + size]
            text = "".join(item.get("text", "") for item in selected)
            normalized = normalize(text)
            if not normalized:
                continue
            windows.append(
                {
                    "chapter": chapter,
                    "seq_start": selected[0]["seq"],
                    "seq_end": selected[-1]["seq"],
                    "items": selected,
                    "normalized": normalized,
                    "grams": grams(normalized),
                }
            )
    return windows


def refine_span(
    target: str,
    weread_chapter: str,
    window: dict[str, Any],
    *,
    exact: bool,
) -> dict[str, Any]:
    """Find the smallest exact span or strongest fuzzy span inside a search window."""
    items = window["items"]
    target_grams = grams(target)
    candidates: list[dict[str, Any]] = []
    for width in range(1, len(items) + 1):
        for start in range(0, len(items) - width + 1):
            selected = items[start : start + width]
            normalized = normalize("".join(str(item.get("text", "")) for item in selected))
            if not normalized:
                continue
            if exact:
                if target not in normalized:
                    continue
                candidates.append(
                    {
                        "seq_start": selected[0]["seq"],
                        "seq_end": selected[-1]["seq"],
                        "span_size": width,
                        "extra_chars": max(0, len(normalized) - len(target)),
                        "sequence": 1.0,
                        "jaccard": 1.0,
                        "chapter_score": similarity(weread_chapter, window["chapter"]),
                        "score": 1.0,
                    }
                )
                continue
            selected_grams = grams(normalized)
            overlap = len(target_grams & selected_grams)
            union = len(target_grams | selected_grams) or 1
            jaccard = overlap / union
            sequence = SequenceMatcher(None, target, normalized, autojunk=False).ratio()
            chapter_score = similarity(weread_chapter, window["chapter"])
            score = sequence * 0.72 + jaccard * 0.20 + chapter_score * 0.08
            candidates.append(
                {
                    "seq_start": selected[0]["seq"],
                    "seq_end": selected[-1]["seq"],
                    "span_size": width,
                    "extra_chars": abs(len(normalized) - len(target)),
                    "sequence": sequence,
                    "jaccard": jaccard,
                    "chapter_score": chapter_score,
                    "score": score,
                }
            )
    if not candidates:
        return {
            "seq_start": window["seq_start"],
            "seq_end": window["seq_end"],
            "span_size": len(items),
            "extra_chars": abs(len(window["normalized"]) - len(target)),
            "sequence": 0.0,
            "jaccard": 0.0,
            "chapter_score": similarity(weread_chapter, window["chapter"]),
            "score": 0.0,
        }
    if exact:
        return min(
            candidates,
            key=lambda item: (
                item["span_size"],
                item["extra_chars"],
                -item["chapter_score"],
                item["seq_start"],
            ),
        )
    return max(
        candidates,
        key=lambda item: (
            item["score"],
            -item["span_size"],
            -item["extra_chars"],
            -item["seq_start"],
        ),
    )


def best_metadata_score(manifest: dict[str, Any], bookmarks: dict[str, Any]) -> dict[str, Any]:
    provided = manifest.get("metadata", {})
    expected = bookmarks.get("book", {}) or {}
    title_score = similarity(str(provided.get("title", "")), str(expected.get("title", "")))
    author_score = similarity(str(provided.get("author", "")), str(expected.get("author", "")))
    available_fields = []
    available_scores = []
    if str(provided.get("title", "")).strip() and str(expected.get("title", "")).strip():
        available_fields.append("title")
        available_scores.append(title_score)
    if str(provided.get("author", "")).strip() and str(expected.get("author", "")).strip():
        available_fields.append("author")
        available_scores.append(author_score)
    combined = sum(available_scores) / len(available_scores) if available_scores else None
    return {
        "score": round(combined, 4) if combined is not None else None,
        "title_score": round(title_score, 4),
        "author_score": round(author_score, 4),
        "available_fields": available_fields,
        "provided": {"title": provided.get("title", ""), "author": provided.get("author", "")},
        "expected": {"title": expected.get("title", ""), "author": expected.get("author", "")},
    }


def align_one(
    highlight: dict[str, Any],
    weread_chapter: str,
    windows: list[dict[str, Any]],
    inverted: dict[str, list[int]],
) -> dict[str, Any]:
    text = str(highlight.get("markText", ""))
    target = normalize(text)
    base = {
        "bookmark_id": highlight.get("bookmarkId"),
        "chapter_uid": highlight.get("chapterUid"),
        "weread_chapter": weread_chapter,
        "text": text,
    }
    if not target:
        return {**base, "status": "unmatched", "score": 0.0}

    exact_indexes = [index for index, window in enumerate(windows) if target in window["normalized"]]
    if exact_indexes:
        refined: dict[tuple[int, int], tuple[dict[str, Any], dict[str, Any]]] = {}
        for index in exact_indexes:
            window = windows[index]
            span = refine_span(target, weread_chapter, window, exact=True)
            key = (int(span["seq_start"]), int(span["seq_end"]))
            refined[key] = (window, span)
        window, span = min(
            refined.values(),
            key=lambda item: (
                item[1]["span_size"],
                item[1]["extra_chars"],
                -item[1]["chapter_score"],
                item[1]["seq_start"],
            ),
        )
        return {
            **base,
            "status": "exact",
            "score": 1.0,
            "matched_chapter": window["chapter"],
            "seq_start": span["seq_start"],
            "seq_end": span["seq_end"],
            "span_size": span["span_size"],
            "chapter_score": round(span["chapter_score"], 4),
        }

    target_grams = grams(target)
    counts: Counter[int] = Counter()
    for gram in target_grams:
        counts.update(inverted.get(gram, []))
    candidates = [index for index, _ in counts.most_common(80)]
    if not candidates:
        candidates = list(range(min(len(windows), 200)))
    ranked_windows: list[tuple[float, int]] = []
    for index in candidates:
        window = windows[index]
        overlap = len(target_grams & window["grams"])
        union = len(target_grams | window["grams"]) or 1
        jaccard = overlap / union
        sequence = SequenceMatcher(None, target, window["normalized"], autojunk=False).ratio()
        chapter_score = similarity(weread_chapter, window["chapter"])
        score = sequence * 0.72 + jaccard * 0.20 + chapter_score * 0.08
        ranked_windows.append((score, index))
    threshold = 0.82 if len(target) < 12 else 0.66
    refined_candidates = [
        (windows[index], refine_span(target, weread_chapter, windows[index], exact=False))
        for _, index in sorted(ranked_windows, reverse=True)[:12]
    ]
    if not refined_candidates:
        return {**base, "status": "unmatched", "score": 0.0}
    window, span = max(
        refined_candidates,
        key=lambda item: (
            item[1]["score"],
            -item[1]["span_size"],
            -item[1]["extra_chars"],
        ),
    )
    if span["score"] < threshold:
        return {**base, "status": "unmatched", "score": round(span["score"], 4)}
    return {
        **base,
        "status": "near",
        "score": round(span["score"], 4),
        "matched_chapter": window["chapter"],
        "seq_start": span["seq_start"],
        "seq_end": span["seq_end"],
        "span_size": span["span_size"],
        "sequence_score": round(span["sequence"], 4),
        "trigram_score": round(span["jaccard"], 4),
        "chapter_score": round(span["chapter_score"], 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--paragraphs", type=Path, required=True)
    parser.add_argument("--bookmarks", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--window-size", type=int, default=12)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    bookmarks = json.loads(args.bookmarks.read_text(encoding="utf-8"))
    paragraphs = read_jsonl(args.paragraphs)
    windows = make_windows(paragraphs, max(1, args.window_size))
    inverted: dict[str, list[int]] = defaultdict(list)
    for index, window in enumerate(windows):
        for gram in window["grams"]:
            inverted[gram].append(index)

    chapter_map = {
        str(item.get("chapterUid")): str(item.get("title", ""))
        for item in bookmarks.get("chapters", [])
    }
    alignments = [
        align_one(
            item,
            chapter_map.get(str(item.get("chapterUid")), f"chapterUid {item.get('chapterUid') or ''}"),
            windows,
            inverted,
        )
        for item in bookmarks.get("updated", [])
    ]
    counts = Counter(item["status"] for item in alignments)
    total = len(alignments)
    matched = counts["exact"] + counts["near"]
    match_rate = matched / total if total else 0.0
    exact_rate = counts["exact"] / total if total else 0.0
    metadata = best_metadata_score(manifest, bookmarks)
    metadata_score = metadata["score"]
    metadata_missing = metadata_score is None
    if total < 8:
        compatible = match_rate >= 0.9 and (metadata_missing or metadata_score >= 0.75)
        partial = match_rate >= 0.6 and (metadata_missing or metadata_score >= 0.45)
    else:
        compatible = match_rate >= 0.85 and (metadata_missing or metadata_score >= 0.55)
        partial = match_rate >= 0.55 and (metadata_missing or metadata_score >= 0.35)
    compatibility = "compatible" if compatible else "partial" if partial else "incompatible"
    report = {
        "schema_version": 1,
        "manifest": str(args.manifest.resolve()),
        "paragraphs": str(args.paragraphs.resolve()),
        "bookmarks": str(args.bookmarks.resolve()),
        "metadata_alignment": metadata,
        "summary": {
            "highlight_count": total,
            "exact": counts["exact"],
            "near": counts["near"],
            "unmatched": counts["unmatched"],
            "exact_rate": round(exact_rate, 4),
            "match_rate": round(match_rate, 4),
            "compatibility": compatibility,
            "requires_confirmation": compatibility != "compatible",
            "low_sample_warning": total < 8,
            "metadata_missing": metadata_missing,
        },
        "alignments": alignments,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out.resolve()), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
