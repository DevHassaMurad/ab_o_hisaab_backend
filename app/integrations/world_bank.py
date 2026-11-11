"""World Bank indicator API: national reference values only (no district series exists for Pakistan)."""
from __future__ import annotations

import httpx

from app.core.config import settings


def fetch_indicator(code: str, year_from: int, year_to: int, country: str = "PAK",
                    http: httpx.Client | None = None) -> dict[int, float]:
    client = http or httpx.Client(timeout=30)
    url = f"{settings.WORLD_BANK_BASE_URL}/country/{country}/indicator/{code}"
    r = client.get(url, params={"format": "json", "date": f"{year_from}:{year_to}", "per_page": 100})
    r.raise_for_status()
    doc = r.json()
    rows = doc[1] if isinstance(doc, list) and len(doc) > 1 and doc[1] else []
    return {int(x["date"]): float(x["value"]) for x in rows if x.get("value") is not None}
