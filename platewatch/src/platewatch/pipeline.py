"""Image -> detect -> crop/read -> normalise -> tag -> store."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from platewatch.contracts import PlateRecord, ScanSummary
from platewatch.core.store import PlateStore
from platewatch.core.text import normalize_plate, tag_plate
from platewatch.vision.protocols import Detector, Reader

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_PIXELS = 25_000_000  # reject decompression bombs
MAX_PLATES_PER_IMAGE = 20


class PlatePipeline:
    def __init__(self, detector: Detector, reader: Reader, store: PlateStore) -> None:
        self.detector, self.reader, self.store = detector, reader, store

    def process_file(self, path: Path, source: str) -> list[PlateRecord]:
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        with Image.open(path) as img:
            if img.width * img.height > MAX_PIXELS:
                raise ValueError(f"{source}: image too large")
            img.load()
            image = img.convert("RGB")
        detections = sorted(self.detector.detect(image), key=lambda d: -d.confidence)
        records = []
        for det in detections[:MAX_PLATES_PER_IMAGE]:
            box = det.box
            if box.x_max > image.width or box.y_max > image.height:
                continue  # detector returned a box outside the image
            raw, ocr_conf = self.reader.read(image, box)
            plate = normalize_plate(raw)
            if not plate:
                continue
            conf = round(det.confidence * ocr_conf, 4)
            pid = hashlib.sha256(f"{sha}{box.as_tuple()}".encode()).hexdigest()[:16]
            records.append(
                PlateRecord(
                    id=pid,
                    image_sha256=sha,
                    source=source,
                    box=box,
                    raw_text=raw,
                    plate_text=plate,
                    detection_confidence=det.confidence,
                    ocr_confidence=ocr_conf,
                    confidence=conf,
                    tags=tag_plate(plate, conf),
                )
            )
        return records

    def scan_inbox(self, inbox: Path) -> ScanSummary:
        """Process images in ``inbox`` not seen before (by content hash). Idempotent."""
        files = sorted(
            p for p in inbox.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
        new = failed = stored = 0
        failures: list[str] = []
        for path in files:
            source = path.relative_to(inbox).as_posix()
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if self.store.has_image(sha):
                continue
            try:
                records = self.process_file(path, source)
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                failed += 1
                failures.append(f"{source}: {type(exc).__name__}")
                continue
            new += 1
            stored += sum(self.store.add(r) for r in records)
            self.store.mark_image(sha, source)
        return ScanSummary(
            images_seen=len(files),
            images_new=new,
            images_failed=failed,
            plates_stored=stored,
            failures=failures[:10],
        )
