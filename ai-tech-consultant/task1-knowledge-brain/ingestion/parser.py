"""HTML -> normalized Article. Keeps heading structure and the text surrounding each screenshot."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "iframe", "svg"]
HEADINGS = {"h1", "h2", "h3", "h4"}


@dataclass
class ImageRef:
    url: str
    alt: str
    context: str  # text immediately around the image
    section: str


@dataclass
class Section:
    heading: str
    blocks: list[str] = field(default_factory=list)  # paragraphs, list items, table rows
    images: list[ImageRef] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.blocks).strip()


@dataclass
class Article:
    url: str
    article_id: str
    title: str
    sections: list[Section]
    content_hash: str

    @property
    def images(self) -> list[ImageRef]:
        return [i for s in self.sections for i in s.images]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _table_to_rows(table: Tag) -> list[str]:
    rows = [[_clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:
        return [" | ".join(header)]
    return [", ".join(f"{h}: {v}" for h, v in zip(header, r)) for r in body]  # header travels with every row


def _is_useful_image(img: Tag, src: str) -> bool:
    if not src or src.startswith("data:") or src.lower().endswith(".svg"):
        return False
    for attr in ("width", "height"):
        v = img.get(attr)
        if v and str(v).isdigit() and int(v) < 48:
            return False
    return not re.search(r"(logo|icon|avatar|sprite|pixel)", urlparse(src).path.lower())


def parse_article(url: str, html: str) -> Article:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(NOISE_TAGS):
        t.decompose()
    h1 = soup.find("h1")
    title = _clean(h1.get_text(" ")) if h1 else _clean(soup.title.get_text()) if soup.title else url
    root = soup.find("article") or soup.find("main") or soup.find(id=re.compile("content|article", re.I)) or soup.body or soup
    sections = [Section(heading=title)]

    def add_block(text: str) -> None:
        text = _clean(text)
        if text:
            sections[-1].blocks.append(text)

    for el in root.descendants:
        if not isinstance(el, Tag):
            continue
        if el.name in HEADINGS and el is not h1:
            sections.append(Section(heading=_clean(el.get_text(" "))))
        elif el.name == "p" and not el.find_parent(["li", "table"]):
            add_block(el.get_text(" "))
        elif el.name == "li":
            add_block("- " + el.get_text(" "))
        elif el.name == "table":
            for row in _table_to_rows(el):
                add_block(row)
        elif el.name == "img":
            src = el.get("src") or el.get("data-src") or ""
            if _is_useful_image(el, src):
                sec = sections[-1]
                prev = sec.blocks[-1] if sec.blocks else ""
                sec.images.append(ImageRef(urljoin(url, src), _clean(el.get("alt", "")), prev[:400], sec.heading))

    sections = [s for s in sections if s.blocks or s.images]
    normalized = "\n".join(f"## {s.heading}\n{s.text}\n" + "\n".join(i.url for i in s.images) for s in sections)
    content_hash = hashlib.sha256(normalized.encode()).hexdigest()
    article_id = hashlib.sha1(url.encode()).hexdigest()[:12]
    return Article(url, article_id, title, sections, content_hash)
