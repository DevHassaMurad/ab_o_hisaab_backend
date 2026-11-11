"""Bounded decoding, geometric correction and quality measurements, not authenticity."""

import io
from contextlib import closing
import math
import os
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
from PIL import Image, ImageOps

PDF_LOCK = Lock()  # PDFium must not run concurrently, even for different documents.
MAX_PIXELS = int(os.environ.get("DOCUMENT_MAX_PIXELS", "24000000"))
MAX_PAGES = int(os.environ.get("DOCUMENT_MAX_PDF_PAGES", "3"))


class DocumentProcessingError(ValueError):
    pass


def decode_pages(path, content_type):
    path = Path(path)
    if content_type in ("image/jpeg", "image/png"):
        try:
            with Image.open(path) as image:
                expected = "JPEG" if content_type == "image/jpeg" else "PNG"
                if image.format != expected or getattr(image, "n_frames", 1) != 1:
                    raise DocumentProcessingError("Upload a single PNG or JPEG image.")
                if image.width * image.height > MAX_PIXELS:
                    raise DocumentProcessingError(
                        "Image resolution exceeds the processing limit."
                    )
                image.verify()
            with Image.open(path) as image:
                image.load()
                return [
                    np.asarray(ImageOps.exif_transpose(image).convert("RGB"))[
                        :, :, ::-1
                    ].copy()
                ]
        except DocumentProcessingError:
            raise
        except Exception as exc:
            raise DocumentProcessingError(
                "This image is damaged or cannot be decoded."
            ) from exc
    if content_type != "application/pdf":
        raise DocumentProcessingError("Only PDF, PNG and JPEG documents are supported.")
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw

        # These actions are never executed. Reject known active/embedded content too.
        data = path.read_bytes()
        if any(
            marker in data
            for marker in (
                b"/JavaScript",
                b"/Launch",
                b"/EmbeddedFile",
                b"/RichMedia",
                b"/OpenAction",
            )
        ):
            raise DocumentProcessingError(
                "PDFs with scripts, actions or embedded files are not supported."
            )
        pages = []
        with PDF_LOCK, pdfium.PdfDocument(data) as pdf:
            if not 1 <= len(pdf) <= MAX_PAGES:
                raise DocumentProcessingError(f"PDF must contain 1–{MAX_PAGES} pages.")
            if raw.FPDFDoc_GetJavaScriptActionCount(pdf) or pdf.count_attachments():
                raise DocumentProcessingError(
                    "Active or embedded PDF content is not supported."
                )
            for index in range(len(pdf)):
                with closing(pdf[index]) as page:
                    width, height = page.get_size()
                    if min(width, height) <= 0 or width * height > MAX_PIXELS:
                        raise DocumentProcessingError(
                            "PDF page dimensions exceed the processing limit."
                        )
                    scale = min(
                        2.5,
                        2400 / max(width, height),
                        math.sqrt(MAX_PIXELS / (width * height)),
                    )
                    bitmap = page.render(scale=scale, may_draw_forms=False)
                    try:
                        pages.append(
                            np.asarray(bitmap.to_pil().convert("RGB"))[
                                :, :, ::-1
                            ].copy()
                        )
                    finally:
                        bitmap.close()
        return pages
    except DocumentProcessingError:
        raise
    except Exception as exc:
        raise DocumentProcessingError(
            "This PDF is damaged, encrypted or cannot be rendered."
        ) from exc


def preview_png(path, content_type):
    page = decode_pages(path, content_type)[0]
    scale = min(1, 1000 / max(page.shape[:2]))
    image = Image.fromarray(cv2.cvtColor(page, cv2.COLOR_BGR2RGB))
    image = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class DocumentPreprocessor:
    def prepare(self, image, orientation=0):
        if orientation in (90, 180, 270):
            image = cv2.rotate(
                image,
                {
                    90: cv2.ROTATE_90_CLOCKWISE,
                    180: cv2.ROTATE_180,
                    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
                }[orientation],
            )
        scale = min(1, 2200 / max(image.shape[:2]))
        image = cv2.resize(
            image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
        )
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        boundary_found = False
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
            polygon = cv2.approxPolyDP(
                contour, 0.02 * cv2.arcLength(contour, True), True
            )
            ratio = cv2.contourArea(contour) / (width * height)
            if (
                len(polygon) == 4
                and 0.25 < ratio < 0.98
                and cv2.isContourConvex(polygon)
            ):
                points = polygon.reshape(4, 2).astype("float32")
                sums, diffs = points.sum(axis=1), np.diff(points, axis=1).ravel()
                ordered = np.array(
                    [
                        points[sums.argmin()],
                        points[diffs.argmin()],
                        points[sums.argmax()],
                        points[diffs.argmax()],
                    ],
                    dtype="float32",
                )
                top_left, top_right, bottom_right, bottom_left = ordered
                out_w = int(
                    max(
                        np.linalg.norm(top_right - top_left),
                        np.linalg.norm(bottom_right - bottom_left),
                    )
                )
                out_h = int(
                    max(
                        np.linalg.norm(bottom_left - top_left),
                        np.linalg.norm(bottom_right - top_right),
                    )
                )
                if min(out_w, out_h) > 100:
                    target = np.array(
                        [
                            [0, 0],
                            [out_w - 1, 0],
                            [out_w - 1, out_h - 1],
                            [0, out_h - 1],
                        ],
                        dtype="float32",
                    )
                    image = cv2.warpPerspective(
                        image,
                        cv2.getPerspectiveTransform(ordered, target),
                        (out_w, out_h),
                    )
                    boundary_found = True
                    break
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Small skew is measured from horizontal line segments; large rotations use OCR OSD.
        lines = cv2.HoughLinesP(
            cv2.Canny(gray, 50, 150),
            1,
            np.pi / 180,
            80,
            minLineLength=max(100, gray.shape[1] // 4),
            maxLineGap=20,
        )
        angles = (
            []
            if lines is None
            else [
                math.degrees(math.atan2(y2 - y1, x2 - x1))
                for [[x1, y1, x2, y2]] in lines
            ]
        )
        angles = [angle for angle in angles if abs(angle) < 12]
        skew = float(np.median(angles)) if angles else 0.0
        if abs(skew) > 0.4:
            matrix = cv2.getRotationMatrix2D(
                (gray.shape[1] / 2, gray.shape[0] / 2), skew, 1
            )
            gray = cv2.warpAffine(
                gray, matrix, (gray.shape[1], gray.shape[0]), borderValue=255
            )
        metrics = {
            "width": gray.shape[1],
            "height": gray.shape[0],
            "boundary_detected": boundary_found,
            "rotation": orientation,
            "skew_degrees": round(skew, 2),
            "blur_score": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
            "brightness": round(float(gray.mean()), 2),
            "contrast": round(float(gray.std()), 2),
        }
        issues = []
        if min(gray.shape) < 250 or max(gray.shape) < 500:
            issues.append("The document resolution is too low.")
        if metrics["blur_score"] < float(
            os.environ.get("DOCUMENT_MIN_BLUR_SCORE", "25")
        ):
            issues.append("The document is blurred or lacks readable detail.")
        if metrics["brightness"] < 30 or metrics["brightness"] > 253:
            issues.append("The document is too dark or overexposed.")
        if metrics["contrast"] < 10:
            issues.append("The document has insufficient contrast.")
        enhanced = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(gray)
        enhanced = cv2.GaussianBlur(enhanced, (3, 3), 0)
        enhanced = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[
            1
        ]
        return enhanced, metrics, issues
