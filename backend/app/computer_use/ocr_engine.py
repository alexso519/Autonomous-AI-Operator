"""
OCR engine — Tesseract with EasyOCR fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

_tesseract_available = False
_easyocr_available = False

try:
    import pytesseract  # type: ignore
    _tesseract_available = True
except ImportError:
    pytesseract = None  # type: ignore

try:
    import easyocr  # type: ignore
    _easyocr_available = True
except ImportError:
    easyocr = None  # type: ignore


@dataclass
class OCRRegion:
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence,
        }


@dataclass
class OCRResult:
    engine: str
    text: str
    regions: list[OCRRegion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "text": self.text,
            "regions": [r.to_dict() for r in self.regions],
        }


class OCREngine:
    """Extract text from screenshots using configured OCR backend."""

    _easyocr_reader: Any = None

    def __init__(self, engine: str | None = None) -> None:
        self.engine = (engine or settings.ocr_engine).lower()

    @classmethod
    def available_engines(cls) -> list[str]:
        engines: list[str] = ["heuristic"]
        if _tesseract_available:
            engines.append("tesseract")
        if _easyocr_available:
            engines.append("easyocr")
        return engines

    def _get_easyocr_reader(self) -> Any:
        if self._easyocr_reader is None and _easyocr_available:
            self._easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        return self._easyocr_reader

    def extract(self, image_path: str) -> OCRResult:
        if self.engine == "tesseract" and _tesseract_available:
            return self._extract_tesseract(image_path)
        if self.engine == "easyocr" and _easyocr_available:
            return self._extract_easyocr(image_path)
        if self.engine != "heuristic":
            if _tesseract_available:
                return self._extract_tesseract(image_path)
            if _easyocr_available:
                return self._extract_easyocr(image_path)
        return self._extract_heuristic(image_path)

    def _load_image(self, image_path: str) -> Any:
        from PIL import Image
        return Image.open(image_path)

    def _extract_tesseract(self, image_path: str) -> OCRResult:
        img = self._load_image(image_path)
        text = pytesseract.image_to_string(img)
        regions: list[OCRRegion] = []
        try:
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            n = len(data["text"])
            for i in range(n):
                word = (data["text"][i] or "").strip()
                if not word:
                    continue
                conf = float(data["conf"][i]) if data["conf"][i] != "-1" else 0.0
                regions.append(OCRRegion(
                    text=word,
                    x=int(data["left"][i]),
                    y=int(data["top"][i]),
                    width=int(data["width"][i]),
                    height=int(data["height"][i]),
                    confidence=conf / 100.0 if conf > 1 else conf,
                ))
        except Exception as exc:
            logger.debug("Tesseract region extraction failed: %s", exc)
        return OCRResult(engine="tesseract", text=text.strip(), regions=regions)

    def _extract_easyocr(self, image_path: str) -> OCRResult:
        reader = self._get_easyocr_reader()
        if reader is None:
            return self._extract_heuristic(image_path)
        results = reader.readtext(image_path)
        regions: list[OCRRegion] = []
        lines: list[str] = []
        for bbox, text, conf in results:
            xs = [int(p[0]) for p in bbox]
            ys = [int(p[1]) for p in bbox]
            x, y = min(xs), min(ys)
            w, h = max(xs) - x, max(ys) - y
            regions.append(OCRRegion(text=text, x=x, y=y, width=w, height=h, confidence=float(conf)))
            lines.append(text)
        return OCRResult(engine="easyocr", text="\n".join(lines), regions=regions)

    def _extract_heuristic(self, image_path: str) -> OCRResult:
        """Fallback when no OCR library is installed — empty result."""
        logger.info("Using heuristic OCR fallback for %s", image_path)
        return OCRResult(engine="heuristic", text="", regions=[])
