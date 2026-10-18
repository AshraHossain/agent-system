"""SQLite-backed vector store with brute-force cosine search.

Honest limits: a full scan with numpy is fine up to roughly 10^5 plates; beyond that move to a
dedicated ANN index (Qdrant, pgvector, ...) behind the same ``PlateStore`` surface.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import numpy as np

from platewatch.contracts import BoundingBox, PlateRecord, SearchHit
from platewatch.core.embedding import Embedder, HashingEmbedder
from platewatch.core.text import normalize_plate

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plates (
  id TEXT PRIMARY KEY, image_sha256 TEXT NOT NULL, source TEXT NOT NULL, box TEXT NOT NULL,
  raw_text TEXT NOT NULL, plate_text TEXT NOT NULL, det_conf REAL NOT NULL, ocr_conf REAL NOT NULL,
  confidence REAL NOT NULL, tags TEXT NOT NULL, embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS plates_image ON plates(image_sha256);
CREATE TABLE IF NOT EXISTS images (image_sha256 TEXT PRIMARY KEY, source TEXT NOT NULL);
"""


class PlateStore:
    def __init__(self, path: str | Path = ":memory:", embedder: Embedder | None = None) -> None:
        self._embedder = embedder or HashingEmbedder()
        # PydanticAI runs sync tools in worker threads; serialise access with a lock.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.RLock()
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def has_image(self, image_sha256: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT 1 FROM images WHERE image_sha256=?", (image_sha256,))
            return row.fetchone() is not None

    def mark_image(self, image_sha256: str, source: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO images VALUES (?, ?)", (image_sha256, source))
            self._db.commit()

    def add(self, rec: PlateRecord) -> bool:
        """Insert idempotently. Returns True if the record was new."""
        with self._lock:
            emb = self._embedder.embed(rec.plate_text).astype(np.float32).tobytes()
            cur = self._db.execute(
                "INSERT OR IGNORE INTO plates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.id,
                    rec.image_sha256,
                    rec.source,
                    json.dumps(rec.box.as_tuple()),
                    rec.raw_text,
                    rec.plate_text,
                    rec.detection_confidence,
                    rec.ocr_confidence,
                    rec.confidence,
                    json.dumps(rec.tags),
                    emb,
                ),
            )
            self._db.commit()
            return cur.rowcount == 1

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM plates").fetchone()[0])

    def get(self, plate_id: str) -> PlateRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id,image_sha256,source,box,raw_text,plate_text,det_conf,ocr_conf,"
                "confidence,tags FROM plates WHERE id=?",
                (plate_id,),
            ).fetchone()
            return self._row_to_record(row) if row else None

    def search(self, query: str, limit: int = 5, min_score: float = 0.3) -> list[SearchHit]:
        with self._lock:
            q = normalize_plate(query)
            if not q:
                return []
            rows = self._db.execute(
                "SELECT id,source,plate_text,confidence,tags,embedding FROM plates"
            ).fetchall()
            if not rows:
                return []
            # Count sightings per plate_text across all stored records.
            from collections import Counter

            text_counts: Counter[str] = Counter(r[2] for r in rows)
            matrix = np.stack([np.frombuffer(r[5], dtype=np.float32) for r in rows])
            scores = matrix @ self._embedder.embed(q)
            order = np.argsort(-scores)[: max(1, min(limit, 50))]
            hits = []
            for i in order:
                pid, source, text, conf, tags, _ = rows[int(i)]
                score = float(scores[int(i)])
                if text != q and score < min_score:
                    continue
                hits.append(
                    SearchHit(
                        plate_id=pid,
                        plate_text=text,
                        source=source,
                        score=round(max(-1.0, min(1.0, score)), 4),
                        exact=text == q,
                        confidence=conf,
                        tags=json.loads(tags),
                        sightings=text_counts[text],
                    )
                )
            hits.sort(key=lambda h: (not h.exact, -h.score))
            return hits

    def needs_review(self, limit: int = 20) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM plates WHERE tags LIKE '%needs-review%' ORDER BY id LIMIT ?",
                (limit,),
            )
            return [r[0] for r in rows]

    @staticmethod
    def _row_to_record(r: tuple) -> PlateRecord:
        return PlateRecord(
            id=r[0],
            image_sha256=r[1],
            source=r[2],
            box=BoundingBox(
                **dict(zip(("x_min", "y_min", "x_max", "y_max"), json.loads(r[3]), strict=True))
            ),
            raw_text=r[4],
            plate_text=r[5],
            detection_confidence=r[6],
            ocr_confidence=r[7],
            confidence=r[8],
            tags=json.loads(r[9]),
        )
