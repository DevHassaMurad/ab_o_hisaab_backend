"""Private local storage. Never mount this directory as static/public content."""

import os
from pathlib import Path
from uuid import uuid4

from controller.authentication import AuthFlowError

MAX_FILE_BYTES = int(os.environ.get('DOCUMENT_MAX_FILE_BYTES', str(10 * 1024 * 1024)))
MAX_REQUEST_BYTES = int(os.environ.get('DOCUMENT_MAX_REQUEST_BYTES', str(64 * 1024 * 1024)))
CONTENT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}


class DocumentStorage:
    def __init__(self):
        default = Path(__file__).resolve().parents[1] / "data" / "private_documents"
        self.root = Path(os.environ.get("DOCUMENT_STORAGE_DIR") or default).resolve()

    def path(self, reference):
        # Only server-generated references can be resolved, never URLs supplied by a client.
        path = (self.root / reference).resolve()
        if path.parent != self.root or path.name != reference:
            raise AuthFlowError("INVALID_DOCUMENT", "Invalid storage reference.", 400)
        return path

    def save(self, upload):
        suffix = CONTENT_TYPES.get(upload.content_type)
        if not suffix:
            raise AuthFlowError("INVALID_FILE", "Choose a JPG, PNG or PDF file.", 422)
        upload.file.seek(0)
        head = upload.file.read(16)
        valid = (
            (suffix == ".jpg" and head.startswith(b"\xff\xd8\xff"))
            or (suffix == ".png" and head.startswith(b"\x89PNG\r\n\x1a\n"))
            or (suffix == ".pdf" and head.startswith(b"%PDF-"))
        )
        if not valid:
            raise AuthFlowError(
                "INVALID_FILE",
                "The file content does not match its JPG, PNG or PDF type.",
                422,
            )
        reference = uuid4().hex + suffix
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path(reference)
        size = 0
        try:
            with path.open("xb") as target:
                os.chmod(path, 0o600)
                upload.file.seek(0)
                while chunk := upload.file.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise AuthFlowError(
                            "FILE_TOO_LARGE", f"Each file must be at most {MAX_FILE_BYTES // (1024 * 1024)} MB.", 413
                        )
                    target.write(chunk)
            from agents.document_preprocessor import decode_pages, DocumentProcessingError
            try:
                decode_pages(path, upload.content_type)
            except DocumentProcessingError as exc:
                raise AuthFlowError('INVALID_FILE', str(exc), 422) from exc
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return reference, size

    def remove(self, reference):
        self.path(reference).unlink(missing_ok=True)


class UploadSizeLimit:
    """Bound streamed multipart bodies as well as requests carrying Content-Length."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").endswith(
            ("/auth/register", "/documents/upload")
        ):
            return await self.app(scope, receive, send)
        from fastapi.responses import JSONResponse

        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = MAX_REQUEST_BYTES + 1
        if declared > MAX_REQUEST_BYTES:
            return await JSONResponse(
                {
                    "detail": "Request must be at most 64 MB.",
                    "code": "REQUEST_TOO_LARGE",
                },
                413,
            )(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            from starlette.exceptions import HTTPException

            message = await receive()
            total += len(message.get("body", b""))
            if total > MAX_REQUEST_BYTES:
                raise HTTPException(413, "Request must be at most 64 MB.")
            return message

        await self.app(scope, bounded_receive, send)
