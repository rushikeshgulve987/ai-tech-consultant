"""Describe screenshots with a vision model. Descriptions are stored as *searchable evidence with uncertainty*,
never as unquestionable facts."""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.llm import LLM, complete_json
from .crawler import PoliteFetcher
from .parser import ImageRef

SYSTEM = (
    "You describe screenshots from a software help center so they can be searched later. "
    "Only report what is visibly legible. If text is blurry or you are unsure, say so in `uncertain` instead of guessing. "
    "Return ONLY JSON: {\"description\": str (<=60 words), \"labels\": [exact UI labels/buttons/settings visible], "
    "\"uncertain\": bool}."
)
MEDIA = {"image/png": "image/png", "image/jpeg": "image/jpeg", "image/jpg": "image/jpeg", "image/gif": "image/gif", "image/webp": "image/webp"}


@dataclass
class ImageDescription:
    description: str
    labels: list[str]
    uncertain: bool

    def as_text(self, alt: str = "") -> str:
        parts = [f"Screenshot: {self.description}"]
        if self.labels:
            parts.append("Visible labels: " + "; ".join(self.labels))
        if alt:
            parts.append(f"Alt text: {alt}")
        if self.uncertain:
            parts.append("(Some content unclear.)")
        return " ".join(parts)


async def describe_image(llm: LLM, fetcher: PoliteFetcher, ref: ImageRef, model: str, max_bytes: int) -> ImageDescription | None:
    res = await fetcher.fetch(ref.url)
    if not res.ok or len(res.content) > max_bytes:
        return None
    media = MEDIA.get(res.content_type.split(";")[0].strip().lower())
    if media is None:
        return None
    user = f"Article section: {ref.section}\nText just before the image: {ref.context}\nDescribe the screenshot."
    try:
        data, _ = await complete_json(llm, system=SYSTEM, user=user, model=model, max_tokens=400, images=[(res.content, media)])
    except (ValueError, json.JSONDecodeError, RuntimeError):
        return None
    return ImageDescription(str(data.get("description", "")), [str(x) for x in data.get("labels", [])][:15],
                            bool(data.get("uncertain", False)))
