"""Near-duplicate detection (dhash + Hamming distance).

Concept ported from the verified old stack, rewritten against the new
module's contracts. No old module is imported.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

#: Two photos whose dhash differs by at most this many bits are "the same
#: shot" for a listing album.
HASH_SIZE = 16
DEFAULT_THRESHOLD = 6


def dhash(path: Path, size: int = HASH_SIZE) -> np.ndarray:
    """64-bit difference hash as a flat boolean array."""
    with Image.open(path) as im:
        small = im.convert("L").resize((size + 1, size), Image.LANCZOS)
        arr = np.asarray(small, dtype=np.int16)
    diff = arr[:, 1:] > arr[:, :-1]
    return diff.flatten()


def hamming(a: np.ndarray, b: np.ndarray) -> int:
    if a.shape != b.shape:
        raise ValueError("hash shape mismatch")
    return int(np.count_nonzero(a != b))


def find_duplicates(paths: list[Path],
                    threshold: int = DEFAULT_THRESHOLD) -> dict[int, list[int]]:
    """Group near-identical photos.

    Returns a mapping of group index → member indexes, where the first
    member of each group is the "keeper".
    """
    hashes = [dhash(p) for p in paths]
    parent = list(range(len(paths)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            if hamming(hashes[i], hashes[j]) <= threshold:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(len(paths)):
        groups.setdefault(find(i), []).append(i)
    # Drop singletons — they are not duplicates.
    return {k: v for k, v in groups.items() if len(v) > 1}
