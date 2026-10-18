"""Embedders. `fastembed` is the real one (local ONNX, no API key). `hash` is a deterministic stand-in for tests."""
from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    """Bag-of-words hashing into 256 dims. Not semantic; only for offline tests."""

    dim = 256

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class FastEmbedder:
    dim = 384

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding

        self._m = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> np.ndarray:
        arr = np.array(list(self._m.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.where(norms == 0, 1, norms)


def make_embedder(name: str) -> Embedder:
    return HashEmbedder() if name == "hash" else FastEmbedder()
