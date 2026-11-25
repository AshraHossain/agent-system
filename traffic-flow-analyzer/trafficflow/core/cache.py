"""Content-keyed, size-bounded cache for expensive per-series forecast results."""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

import pandas as pd

T = TypeVar("T")


def series_fingerprint(series: pd.Series) -> str:
    """Hex digest of a series' values and index; equal series give equal digests."""
    raw = pd.util.hash_pandas_object(series, index=True).to_numpy().tobytes()
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


class ResultCache:
    """LRU cache whose lookup, compute-on-miss and insert run under one lock."""

    def __init__(self, maxsize: int = 1024) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self._maxsize = maxsize
        self._data: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_compute(self, key: Hashable, compute: Callable[[], T]) -> T:
        # Held during compute(): concurrent misses then compute once. The work is
        # CPU-bound, so the GIL would serialize it anyway.
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            value = compute()
            self._data[key] = value
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
            return value

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
