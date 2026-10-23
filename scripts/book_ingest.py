#!/usr/bin/env python3
"""Inspect, extract, and index an EPUB, PDF, or DRM-free AZW3 ebook."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET


BLOCK_TAGS = {"p", "li", "blockquote", "pre", "dt", "dd", "figcaption"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SUPPORTED = {".epub", ".pdf", ".azw3"}


def die(message: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00a0", " ").replace("\u200b", "")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def safe_component(value: str, fallback: str) -> str:
    value = clean_text(value)
    value = re.sub(r"[\\/:*?\"<>|]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or fallback)[:100]


def run_command(command: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing dependency: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out: {command[0]}") from exc


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class BlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active_tag: str | None = None
        self.depth = 0
        self.parts: list[str] = []
        self.blocks: list[tuple[str, str]] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "math"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in BLOCK_TAGS | HEADING_TAGS:
            if self.active_tag is None:
                self.active_tag = tag
                self.depth = 1
                self.parts = []
            else:
                self.depth += 1
        elif self.active_tag is not None:
            self.depth += 1
        if tag == "br" and self.active_tag is not None:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "math"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth or self.active_tag is None:
            return
        self.depth -= 1
        if self.depth <= 0:
            text = clean_text("".join(self.parts))
            if text:
                self.blocks.append((self.active_tag, text))
            self.active_tag = None
            self.depth = 0
            self.parts = []

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and self.active_tag is not None:
            self.parts.append(data)


def parse_html_blocks(raw: bytes) -> list[tuple[str, str]]:
    text = raw.decode("utf-8", errors="replace")
    parser = BlockParser()
    parser.feed(text)
    return parser.blocks


def find_child_text(root: ET.Element, name: str) -> str:
    for node in root.iter():
        if local_name(node.tag) == name and clean_text(node.text or ""):
            return clean_text(node.text or "")
    return ""


def epub_encrypted_resources(archive: zipfile.ZipFile) -> list[str]:
    name = "META-INF/encryption.xml"
    if name not in archive.namelist():
        return []
    try:
        root = ET.fromstring(archive.read(name))
    except ET.ParseError:
        return ["<unparsed encryption.xml>"]
    resources: list[str] = []
    for node in root.iter():
        if local_name(node.tag) == "CipherReference":
            uri = node.attrib.get("URI") or node.attrib.get("uri")
            if uri:
                resources.append(unquote(uri))
    return resources


def extract_epub(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        broken_member = archive.testzip()
        names = set(archive.namelist())
        if "META-INF/container.xml" not in names:
            raise RuntimeError("EPUB is missing META-INF/container.xml")
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(
            (node.attrib.get("full-path") for node in container.iter() if local_name(node.tag) == "rootfile"),
            None,
        )
        if not rootfile or rootfile not in names:
            raise RuntimeError("EPUB OPF package document is missing")
        opf_root = ET.fromstring(archive.read(rootfile))
        metadata_node = next((node for node in opf_root.iter() if local_name(node.tag) == "metadata"), opf_root)
        metadata = {
            "title": find_child_text(metadata_node, "title"),
            "author": find_child_text(metadata_node, "creator"),
            "language": find_child_text(metadata_node, "language"),
            "identifier": find_child_text(metadata_node, "identifier"),
            "publisher": find_child_text(metadata_node, "publisher"),
            "date": find_child_text(metadata_node, "date"),
        }
        manifest: dict[str, dict[str, str]] = {}
        for node in opf_root.iter():
            if local_name(node.tag) == "item" and node.attrib.get("id") and node.attrib.get("href"):
                manifest[node.attrib["id"]] = {
                    "href": unquote(node.attrib["href"]),
                    "media_type": node.attrib.get("media-type", ""),
                    "properties": node.attrib.get("properties", ""),
                }
        spine_ids = [
            node.attrib["idref"]
            for node in opf_root.iter()
            if local_name(node.tag) == "itemref" and node.attrib.get("idref")
        ]
        opf_dir = PurePosixPath(rootfile).parent
        paragraphs: list[dict[str, Any]] = []
        chapter_seq = 0
        current_chapter = "Front matter"
        empty_spine = 0
        missing_spine = 0
        replacement_chars = 0
        seq = 1
        for item_id in spine_ids:
            item = manifest.get(item_id)
            if not item:
                missing_spine += 1
                continue
            href = str(opf_dir / PurePosixPath(item["href"]))
            if href not in names:
                missing_spine += 1
                continue
            blocks = parse_html_blocks(archive.read(href))
            if not blocks:
                empty_spine += 1
                continue
            first_heading = next((text for tag, text in blocks if tag in HEADING_TAGS), "")
            if first_heading:
                current_chapter = first_heading
                chapter_seq += 1
            elif chapter_seq == 0:
                chapter_seq = 1
                current_chapter = Path(item["href"]).stem
            for tag, text in blocks:
                replacement_chars += text.count("�")
                if tag in HEADING_TAGS:
                    if text != current_chapter:
                        current_chapter = text
                        chapter_seq += 1
                    continue
                if len(text) < 2:
                    continue
                paragraphs.append(
                    {
                        "seq": seq,
                        "chapter_seq": chapter_seq,
                        "chapter": current_chapter,
                        "source": href,
                        "block_type": tag,
                        "text": text,
                    }
                )
                seq += 1
        encrypted = epub_encrypted_resources(archive)
        image_count = sum(
            1
            for item in manifest.values()
            if item["media_type"].startswith("image/") or Path(item["href"]).suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        )
        nav_count = sum(1 for item in manifest.values() if "nav" in item["properties"] or item["media_type"] == "application/x-dtbncx+xml")
        char_count = sum(len(item["text"]) for item in paragraphs)
        score = 100
        issues: list[str] = []
        if broken_member:
            score -= 35
            issues.append(f"Corrupt ZIP member: {broken_member}")
        if not metadata["title"]:
            score -= 8
            issues.append("Missing title metadata")
        if nav_count == 0:
            score -= 8
            issues.append("Missing navigation document")
        if missing_spine:
            score -= min(25, missing_spine * 5)
            issues.append(f"Missing spine items: {missing_spine}")
        if spine_ids and empty_spine / len(spine_ids) > 0.2:
            score -= 15
            issues.append("More than 20% of spine items have no extractable text")
        if char_count < 2000:
            score -= 35
            issues.append("Very little extractable text")
        if encrypted and char_count < 2000:
            score = min(score, 15)
            issues.append("Encrypted resources prevent reliable extraction")
        if char_count and replacement_chars / char_count > 0.005:
            score -= 15
            issues.append("High Unicode replacement-character rate")
        metrics = {
            "score": max(0, score),
            "issues": issues,
            "zip_integrity": broken_member is None,
            "spine_items": len(spine_ids),
            "empty_spine_items": empty_spine,
            "missing_spine_items": missing_spine,
            "navigation_items": nav_count,
            "image_count": image_count,
            "paragraph_count": len(paragraphs),
            "character_count": char_count,
            "replacement_characters": replacement_chars,
            "encrypted_resources": encrypted,
        }
        return metadata, paragraphs, metrics


def parse_pdf_info(raw: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        info[key.strip().lower().replace(" ", "_")] = value.strip()
    return info


def looks_like_heading(text: str) -> bool:
    if not text or len(text) > 90 or "。" in text:
        return False
    return bool(
        re.match(r"^(第[一二三四五六七八九十百千万0-9]+[章节部篇]|chapter\s+\d+|part\s+[ivx0-9]+|序言|前言|引言|结语|后记|附录)", text, re.I)
    )


def extract_pdf(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    info_result = run_command(["pdfinfo", str(path)])
    info = parse_pdf_info(info_result.stdout)
    text_result = run_command(["pdftotext", "-enc", "UTF-8", str(path), "-"])
    raw_text = text_result.stdout
    pages = raw_text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    paragraphs: list[dict[str, Any]] = []
    current_chapter = "PDF pages"
    chapter_seq = 1
    seq = 1
    replacement_chars = raw_text.count("�")
    control_chars = sum(1 for char in raw_text if ord(char) < 32 and char not in "\n\r\t\f")
    for page_number, page in enumerate(pages, 1):
        blocks = re.split(r"\n\s*\n+", page)
        for block in blocks:
            text = clean_text(" ".join(line.strip() for line in block.splitlines() if line.strip()))
            if len(text) < 2:
                continue
            if looks_like_heading(text):
                current_chapter = text
                chapter_seq += 1
                continue
            paragraphs.append(
                {
                    "seq": seq,
                    "chapter_seq": chapter_seq,
                    "chapter": current_chapter,
                    "page": page_number,
                    "source": f"page:{page_number}",
                    "block_type": "p",
                    "text": text,
                }
            )
            seq += 1
    fonts_result = run_command(["pdffonts", str(path)])
    font_lines = [line for line in fonts_result.stdout.splitlines()[2:] if line.strip()]
    char_count = sum(len(item["text"]) for item in paragraphs)
    page_count = int(info.get("pages", len(pages)) or len(pages) or 0)
    chars_per_page = char_count / max(1, page_count)
    score = 100
    issues: list[str] = []
    encrypted = info.get("encrypted", "no").lower().startswith("yes")
    if info_result.returncode != 0:
        score -= 25
        issues.append("pdfinfo could not fully inspect the file")
    if text_result.returncode != 0:
        score -= 40
        issues.append("pdftotext failed")
    if encrypted and char_count < 2000:
        score = min(score, 15)
        issues.append("Encrypted PDF has no usable text layer")
    if chars_per_page < 100:
        score -= 35
        issues.append("Very little text per page; likely scanned or OCR-poor PDF")
    elif chars_per_page < 350:
        score -= 15
        issues.append("Low text density")
    if char_count and replacement_chars / char_count > 0.005:
        score -= 15
        issues.append("High Unicode replacement-character rate")
    if char_count and control_chars / char_count > 0.002:
        score -= 10
        issues.append("High control-character rate")
    metadata = {
        "title": info.get("title", ""),
        "author": info.get("author", ""),
        "publisher": info.get("producer", ""),
        "date": info.get("creationdate", ""),
        "language": "",
        "identifier": "",
    }
    metrics = {
        "score": max(0, score),
        "issues": issues,
        "page_count": page_count,
        "paragraph_count": len(paragraphs),
        "character_count": char_count,
        "characters_per_page": round(chars_per_page, 1),
        "font_count": len(font_lines),
        "encrypted": encrypted,
        "replacement_characters": replacement_chars,
        "control_characters": control_chars,
    }
    return metadata, paragraphs, metrics


def extract_azw3(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    converter = shutil.which("ebook-convert")
    if not converter:
        raise RuntimeError("AZW3 requires Calibre command ebook-convert; provide EPUB/PDF or install Calibre")
    with tempfile.TemporaryDirectory(prefix="reading-synthesis-") as tmp:
        epub_path = Path(tmp) / "converted.epub"
        result = run_command([converter, str(path), str(epub_path)], timeout=300)
        if result.returncode != 0 or not epub_path.exists():
            detail = clean_text(result.stderr)[-500:]
            if "drm" in detail.lower():
                raise RuntimeError("AZW3 appears DRM-protected; DRM circumvention is not supported")
            raise RuntimeError(f"AZW3 conversion failed: {detail or 'unknown error'}")
        metadata, paragraphs, metrics = extract_epub(epub_path)
        metrics["converted_from_azw3"] = True
        metrics["issues"] = ["Text extracted through Calibre conversion"] + metrics["issues"]
        return metadata, paragraphs, metrics


def quality_label(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 55:
        return "fair"
    if score >= 20:
        return "poor"
    return "unreadable"


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_paragraphs(path: Path, paragraphs: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for item in paragraphs:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def build_sqlite(path: Path, paragraphs: list[dict[str, Any]]) -> str:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE paragraphs (seq INTEGER PRIMARY KEY, chapter_seq INTEGER, chapter TEXT, page INTEGER, source TEXT, block_type TEXT, text TEXT)"
    )
    connection.executemany(
        "INSERT INTO paragraphs(seq, chapter_seq, chapter, page, source, block_type, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                item["seq"],
                item.get("chapter_seq"),
                item.get("chapter", ""),
                item.get("page"),
                item.get("source", ""),
                item.get("block_type", "p"),
                item.get("text", ""),
            )
            for item in paragraphs
        ],
    )
    tokenizer = "trigram"
    try:
        connection.execute("CREATE VIRTUAL TABLE paragraphs_fts USING fts5(seq UNINDEXED, chapter, text, tokenize='trigram')")
    except sqlite3.OperationalError:
        tokenizer = "unicode61"
        connection.execute("CREATE VIRTUAL TABLE paragraphs_fts USING fts5(seq UNINDEXED, chapter, text, tokenize='unicode61')")
    connection.executemany(
        "INSERT INTO paragraphs_fts(seq, chapter, text) VALUES (?, ?, ?)",
        [(item["seq"], item.get("chapter", ""), item.get("text", "")) for item in paragraphs],
    )
    connection.commit()
    connection.close()
    return tokenizer


def copy_to_library(source: Path, root: Path, metadata: dict[str, Any], digest: str) -> Path:
    author = safe_component(metadata.get("author", ""), "Unknown author")
    title = safe_component(metadata.get("title", ""), source.stem)
    edition = safe_component(metadata.get("date", ""), "Unknown edition")
    destination_dir = root / author / title / edition
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if destination.exists() and sha256_file(destination) != digest:
        destination = destination_dir / f"{source.stem}-{digest[:8]}{source.suffix.lower()}"
    if not destination.exists():
        shutil.copy2(source, destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="EPUB, PDF, or AZW3 file")
    parser.add_argument("--cache-root", type=Path, default=Path.cwd() / ".book-cache")
    parser.add_argument("--library-root", type=Path)
    parser.add_argument("--copy-original", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Rebuild artifacts even when the file hash is cached")
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    if not source.is_file():
        die(f"Input file does not exist: {source}")
    extension = source.suffix.lower()
    if extension not in SUPPORTED:
        die(f"Unsupported format {extension}; expected EPUB, PDF, or AZW3")
    if args.copy_original and not args.library_root:
        die("--copy-original requires --library-root")

    digest = sha256_file(source)
    cache_dir = args.cache_root.expanduser().resolve() / digest[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "manifest.json"
    if not args.refresh and manifest_path.is_file():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifacts = cached.get("artifacts", {})
            reusable = (
                cached.get("sha256") == digest
                and Path(str(artifacts.get("paragraphs", ""))).is_file()
                and Path(str(artifacts.get("index", ""))).is_file()
            )
        except (OSError, ValueError, TypeError):
            reusable = False
            cached = {}
        if reusable:
            cached["source_path"] = str(source)
            if args.copy_original:
                copied_to = copy_to_library(
                    source,
                    args.library_root.expanduser().resolve(),
                    cached.get("metadata", {}),
                    digest,
                )
                cached["library_path"] = str(copied_to)
            write_json(manifest_path, cached)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "cached": True,
                        "manifest": str(manifest_path),
                        "paragraphs": artifacts["paragraphs"],
                        "index": artifacts["index"],
                        "file_type": cached.get("file_type", extension.lstrip(".")),
                        "title": cached.get("metadata", {}).get("title", ""),
                        "author": cached.get("metadata", {}).get("author", ""),
                        "quality": cached.get("quality", {}),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
    try:
        if extension == ".epub":
            metadata, paragraphs, quality = extract_epub(source)
            file_type = "epub"
        elif extension == ".pdf":
            metadata, paragraphs, quality = extract_pdf(source)
            file_type = "pdf"
        else:
            metadata, paragraphs, quality = extract_azw3(source)
            file_type = "azw3"
    except (RuntimeError, zipfile.BadZipFile, ET.ParseError) as exc:
        die(str(exc))

    quality["label"] = quality_label(int(quality.get("score", 0)))
    paragraphs_path = cache_dir / "paragraphs.jsonl"
    index_path = cache_dir / "index.sqlite"
    write_paragraphs(paragraphs_path, paragraphs)
    tokenizer = build_sqlite(index_path, paragraphs)
    copied_to = None
    if args.copy_original:
        copied_to = copy_to_library(source, args.library_root.expanduser().resolve(), metadata, digest)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source),
        "library_path": str(copied_to) if copied_to else None,
        "sha256": digest,
        "size_bytes": source.stat().st_size,
        "file_type": file_type,
        "mime_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
        "metadata": metadata,
        "quality": quality,
        "artifacts": {
            "paragraphs": str(paragraphs_path),
            "index": str(index_path),
            "fts_tokenizer": tokenizer,
        },
    }
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(manifest_path),
                "paragraphs": str(paragraphs_path),
                "index": str(index_path),
                "file_type": file_type,
                "title": metadata.get("title", ""),
                "author": metadata.get("author", ""),
                "quality": quality,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
