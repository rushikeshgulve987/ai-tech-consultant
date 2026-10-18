"""Sitemap parsing and the new / changed / unchanged / removed diff."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: str | None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def is_valid_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc)


def parse_sitemap(xml_text: str) -> tuple[list[SitemapEntry], list[str]]:
    """Return (page entries, child sitemap urls). Duplicates and invalid URLs are dropped."""
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    entries: dict[str, SitemapEntry] = {}
    children: list[str] = []
    for node in root:
        kind = _local(node.tag)
        loc = lastmod = None
        for c in node:
            if _local(c.tag) == "loc":
                loc = (c.text or "").strip()
            elif _local(c.tag) == "lastmod":
                lastmod = (c.text or "").strip() or None
        if not loc or not is_valid_url(loc):
            continue
        if kind == "sitemap":
            children.append(loc)
        elif kind == "url" and loc not in entries:
            entries[loc] = SitemapEntry(loc, lastmod)
    return list(entries.values()), children


@dataclass
class ManifestDiff:
    new: list[SitemapEntry]
    changed: list[SitemapEntry]
    unchanged: list[SitemapEntry]
    removed: list[str]


def diff_manifest(known: dict[str, dict], current: list[SitemapEntry]) -> ManifestDiff:
    """`known` maps url -> {lastmod, status, active}. A page is only 'unchanged' if it was ingested OK
    and its lastmod is identical and present. Pages without lastmod are re-fetched and compared by content hash."""
    new, changed, unchanged = [], [], []
    seen = set()
    for e in current:
        seen.add(e.url)
        k = known.get(e.url)
        if k is None:
            new.append(e)
        elif not k["active"] or k["status"] != "ok":
            (changed if k["active"] == 0 else new).append(e)  # revived pages count as changed; retries as new work
        elif e.lastmod is None or e.lastmod != k["lastmod"]:
            changed.append(e)
        else:
            unchanged.append(e)
    removed = [u for u, k in known.items() if u not in seen and k["active"]]
    return ManifestDiff(new, changed, unchanged, removed)
