"""Long transcripts are processed in overlapping line windows; every line keeps its number so quotes can be verified."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TChunk:
    start: int   # 1-based, inclusive
    end: int
    text: str    # lines prefixed with "L<n>: "


def numbered_lines(transcript: str) -> list[str]:
    return [l for l in transcript.splitlines() if l.strip()]


def chunk_transcript(transcript: str, size: int = 40, overlap: int = 6) -> list[TChunk]:
    lines = numbered_lines(transcript)
    if not lines:
        return []
    chunks, i = [], 0
    while i < len(lines):
        j = min(len(lines), i + size)
        chunks.append(TChunk(i + 1, j, "\n".join(f"L{n + 1}: {lines[n]}" for n in range(i, j))))
        if j == len(lines):
            break
        i = j - overlap
    return chunks


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s.lower())).strip()


def verify_quote(transcript: str, quote: str) -> tuple[bool, str]:
    """True only if the quote (normalised) appears verbatim in the transcript. Returns the line range where it was found.
    The model's claim is never trusted: this is checked in code."""
    q = _norm(quote)
    if len(q) < 8:
        return False, ""
    lines = numbered_lines(transcript)
    normed = [_norm(l) for l in lines]
    for a in range(len(lines)):          # quotes may span a few consecutive lines
        acc = ""
        for b in range(a, min(len(lines), a + 4)):
            acc = (acc + " " + normed[b]).strip()
            if q in acc:
                return True, f"L{a + 1}" if a == b else f"L{a + 1}-L{b + 1}"
    return False, ""
