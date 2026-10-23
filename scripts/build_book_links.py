#!/usr/bin/env python3
"""Build configured book lookup links from ISBN and book metadata."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse


DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "book-link-providers.json"
TEMPLATE_FIELDS = {"isbn", "isbn10", "isbn13", "title", "author", "publisher"}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalize_isbn(value: Any) -> str:
    candidate = re.sub(r"[^0-9Xx]", "", text(value)).upper()
    return candidate if len(candidate) in {10, 13} else ""


def valid_isbn10(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{9}[0-9X]", value):
        return False
    total = sum((10 - i) * (10 if char == "X" else int(char)) for i, char in enumerate(value))
    return total % 11 == 0


def valid_isbn13(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{13}", value):
        return False
    total = sum(int(char) * (1 if i % 2 == 0 else 3) for i, char in enumerate(value[:12]))
    return (10 - total % 10) % 10 == int(value[-1])


def isbn10_to_13(value: str) -> str:
    core = "978" + value[:9]
    total = sum(int(char) * (1 if i % 2 == 0 else 3) for i, char in enumerate(core))
    return core + str((10 - total % 10) % 10)


def isbn13_to_10(value: str) -> str:
    if not value.startswith("978"):
        return ""
    core = value[3:12]
    total = sum((10 - i) * int(char) for i, char in enumerate(core))
    check = (11 - total % 11) % 11
    return core + ("X" if check == 10 else str(check))


def find_first_isbn(value: Any) -> str:
    if isinstance(value, str):
        for match in re.findall(r"(?:97[89][\s-]?)?\d[\d\s-]{7,15}[\dXx]", value):
            candidate = normalize_isbn(match)
            if (len(candidate) == 10 and valid_isbn10(candidate)) or (
                len(candidate) == 13 and valid_isbn13(candidate)
            ):
                return candidate
        return ""
    if isinstance(value, dict):
        for key in ("isbn13", "isbn", "ISBN", "identifier", "bookIsbn"):
            candidate = find_first_isbn(value.get(key))
            if candidate:
                return candidate
        for nested in value.values():
            candidate = find_first_isbn(nested)
            if candidate:
                return candidate
    if isinstance(value, list):
        for nested in value:
            candidate = find_first_isbn(nested)
            if candidate:
                return candidate
    return ""


def find_metadata(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    candidates = [data]
    for key in ("metadata", "book", "bookInfo", "data"):
        if isinstance(data.get(key), dict):
            candidates.insert(0, data[key])
    result: dict[str, str] = {}
    aliases = {
        "title": ("title", "bookTitle", "name"),
        "author": ("author", "authors", "creator"),
        "publisher": ("publisher", "press"),
    }
    for field, keys in aliases.items():
        for candidate in candidates:
            for key in keys:
                value = candidate.get(key)
                if isinstance(value, list):
                    value = ", ".join(text(item) for item in value if text(item))
                if text(value):
                    result[field] = text(value)
                    break
            if field in result:
                break
    return result


def validate_config(config: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(config, dict) or not isinstance(config.get("providers"), list):
        raise ValueError("Config must contain a providers array")
    providers: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, provider in enumerate(config["providers"], start=1):
        if not isinstance(provider, dict):
            raise ValueError(f"Provider {index} must be an object")
        name = text(provider.get("name"))
        template = text(provider.get("url_template"))
        enabled = provider.get("enabled") is True
        if not name and not template and not enabled:
            warnings.append(f"Provider {index} is an empty disabled slot")
            continue
        if not name:
            raise ValueError(f"Provider {index} is missing name")
        if not template:
            raise ValueError(f"Provider {name!r} is missing url_template")
        parsed = urlparse(template)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Provider {name!r} must use an absolute HTTP(S) URL template")
        fields = set(re.findall(r"\{([a-zA-Z0-9_]+)\}", template))
        unknown = fields - TEMPLATE_FIELDS
        if unknown:
            raise ValueError(f"Provider {name!r} uses unsupported fields: {', '.join(sorted(unknown))}")
        if not fields:
            raise ValueError(f"Provider {name!r} URL template must contain at least one template field")
        providers.append({"name": name, "enabled": enabled, "url_template": template})
    return providers, warnings


def build_links(providers: list[dict[str, Any]], values: dict[str, str]) -> list[dict[str, str]]:
    encoded = {key: quote(value, safe="") for key, value in values.items()}
    links: list[dict[str, str]] = []
    for provider in providers:
        if provider["enabled"]:
            links.append({
                "name": provider["name"],
                "url": provider["url_template"].format(**encoded),
            })
    return links


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--book-json", type=Path, help="WeRead or other saved book metadata JSON")
    parser.add_argument("--isbn")
    parser.add_argument("--title")
    parser.add_argument("--author")
    parser.add_argument("--publisher")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--check-config", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        providers, warnings = validate_config(load_json(args.config))
        if args.check_config:
            print(json.dumps({"ok": True, "providers": providers, "warnings": warnings}, ensure_ascii=False, indent=2))
            return

        sources: list[Any] = []
        if args.manifest:
            sources.append(load_json(args.manifest))
        if args.book_json:
            sources.append(load_json(args.book_json))

        metadata: dict[str, str] = {}
        for source in sources:
            for key, value in find_metadata(source).items():
                metadata.setdefault(key, value)
        for key in ("title", "author", "publisher"):
            direct = text(getattr(args, key))
            if direct:
                metadata[key] = direct

        isbn = normalize_isbn(args.isbn)
        if isbn and not ((len(isbn) == 10 and valid_isbn10(isbn)) or (len(isbn) == 13 and valid_isbn13(isbn))):
            raise ValueError(f"Invalid ISBN checksum: {isbn}")
        if not isbn:
            for source in sources:
                isbn = find_first_isbn(source)
                if isbn:
                    break
        if not isbn:
            raise ValueError("No valid ISBN found; provide --isbn, --manifest, or --book-json")

        isbn10 = isbn if len(isbn) == 10 else isbn13_to_10(isbn)
        isbn13 = isbn if len(isbn) == 13 else isbn10_to_13(isbn)
        values = {
            "isbn": isbn,
            "isbn10": isbn10,
            "isbn13": isbn13,
            "title": metadata.get("title", ""),
            "author": metadata.get("author", ""),
            "publisher": metadata.get("publisher", ""),
        }
        links = build_links(providers, values)
        if not links:
            warnings.append("No enabled link provider is configured")
        result = {"ok": True, "book": values, "links": links, "warnings": warnings}
        if args.format == "markdown":
            if links:
                label = metadata.get("title") or isbn13 or isbn
                for link in links:
                    print(f"[{link['name']}：{label}]({link['url']})")
            else:
                print("未配置可用的图书网站链接。")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
