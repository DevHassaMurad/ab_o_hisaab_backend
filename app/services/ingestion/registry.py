from __future__ import annotations

from app.core.errors import Unprocessable
from app.services.ingestion.base import Ingestor

_registry: dict[str, Ingestor] = {}


def register_ingestor(ingestor: Ingestor) -> None:
    _registry[str(ingestor.source)] = ingestor


def get_ingestor(source: str) -> Ingestor:
    try:
        return _registry[str(source)]
    except KeyError:
        raise Unprocessable("UNKNOWN_SOURCE", f"No ingestor registered for {source}.") from None


def all_ingestors() -> list[Ingestor]:
    return list(_registry.values())
