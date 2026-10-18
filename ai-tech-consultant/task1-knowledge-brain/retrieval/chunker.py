"""Heading-first chunking. Each chunk carries title, heading, url so it can be cited on its own."""
from __future__ import annotations

from dataclasses import dataclass

from ingestion.parser import Article


@dataclass
class Chunk:
    id: str
    url: str
    kind: str
    heading: str
    text: str
    image_url: str | None = None


def split_text(blocks: list[str], max_chars: int, overlap_blocks: int = 1) -> list[list[str]]:
    """Group blocks (paragraphs/list items/table rows) up to max_chars, overlapping by whole blocks so a step
    and its prerequisite are not separated. A single oversized block is kept whole."""
    groups: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for b in blocks:
        if cur and size + len(b) > max_chars:
            groups.append(cur)
            cur = cur[-overlap_blocks:] if overlap_blocks else []
            size = sum(len(x) for x in cur)
        cur.append(b)
        size += len(b)
    if cur and (not groups or cur != groups[-1][-len(cur):]):
        groups.append(cur)
    return groups


def chunk_article(article: Article, image_texts: dict[str, str], max_chars: int = 1200) -> list[Chunk]:
    """`image_texts` maps image url -> searchable description. Chunk ids are deterministic (article_id:n), so
    re-processing the same article overwrites rather than duplicates."""
    chunks: list[Chunk] = []
    n = 0
    for sec in article.sections:
        for group in split_text(sec.blocks, max_chars) if sec.blocks else []:
            body = "\n".join(group)
            chunks.append(Chunk(f"{article.article_id}:{n}", article.url, "text", sec.heading,
                                f"{article.title} > {sec.heading}\n{body}"))
            n += 1
        for img in sec.images:
            desc = image_texts.get(img.url)
            if desc:
                chunks.append(Chunk(f"{article.article_id}:{n}", article.url, "image", sec.heading,
                                    f"{article.title} > {sec.heading}\n{desc}", image_url=img.url))
                n += 1
    return chunks
