#!/usr/bin/env python3
"""Search an extracted ebook SQLite index and return compact paragraph context."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--context", type=int, default=1, help="Neighbor paragraphs before and after")
    args = parser.parse_args()

    connection = sqlite3.connect(args.index)
    connection.row_factory = sqlite3.Row
    pattern = f"%{args.query}%"
    rows = connection.execute(
        "SELECT seq, chapter_seq, chapter, page, source, text FROM paragraphs WHERE text LIKE ? OR chapter LIKE ? ORDER BY seq LIMIT ?",
        (pattern, pattern, max(1, args.limit)),
    ).fetchall()
    results = []
    for row in rows:
        start = max(1, int(row["seq"]) - max(0, args.context))
        end = int(row["seq"]) + max(0, args.context)
        context_rows = connection.execute(
            "SELECT seq, chapter, page, text FROM paragraphs WHERE seq BETWEEN ? AND ? AND chapter = ? ORDER BY seq",
            (start, end, row["chapter"]),
        ).fetchall()
        results.append(
            {
                "match_seq": row["seq"],
                "chapter": row["chapter"],
                "page": row["page"],
                "context": [dict(item) for item in context_rows],
            }
        )
    connection.close()
    print(json.dumps({"query": args.query, "count": len(results), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
