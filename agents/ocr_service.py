"""Real Tesseract LSTM OCR; no raw text is logged or returned to the browser."""

import os
from pathlib import Path
import shutil

import cv2
import pytesseract
from pytesseract import Output


class OCRUnavailable(RuntimeError):
    pass


class OCRService:
    def __init__(self):
        executable = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
        if (
            not executable
            and Path("C:/Program Files/Tesseract-OCR/tesseract.exe").is_file()
        ):
            executable = "C:/Program Files/Tesseract-OCR/tesseract.exe"
        if not executable:
            raise OCRUnavailable("Tesseract is not installed or configured.")
        pytesseract.pytesseract.tesseract_cmd = executable
        self.language = os.environ.get("DOCUMENT_OCR_LANG", "eng")
        self.timeout = int(os.environ.get("DOCUMENT_OCR_TIMEOUT_SECONDS", "20"))

    def orientation(self, image):
        try:
            result = pytesseract.image_to_osd(
                cv2.cvtColor(image, cv2.COLOR_BGR2RGB),
                output_type=Output.DICT,
                timeout=5,
            )
            return (
                int(result["rotate"])
                if float(result.get("orientation_conf", 0)) >= 2
                else 0
            )
        except (pytesseract.TesseractError, RuntimeError):
            return 0  # Sparse documents may not contain enough text for OSD.

    def extract(self, image):
        try:
            data = pytesseract.image_to_data(
                image,
                lang=self.language,
                config="--oem 1 --psm 6",
                output_type=Output.DICT,
                timeout=self.timeout,
            )
        except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as exc:
            raise OCRUnavailable(
                "OCR could not run; check Tesseract and language data."
            ) from exc
        words, confidence, lines = [], [], {}
        for index, raw in enumerate(data["text"]):
            word = raw.strip()
            conf = float(data["conf"][index])
            if not word or conf < 0:
                continue
            words.append(word)
            confidence.append(conf)
            key = (
                data["block_num"][index],
                data["par_num"][index],
                data["line_num"][index],
            )
            lines.setdefault(key, []).append(word)
        return {
            "text": "\n".join(" ".join(words) for words in lines.values()),
            "confidence": round(sum(confidence) / len(confidence), 2)
            if confidence
            else 0,
            "word_count": len(words),
            "line_count": len(lines),
        }
