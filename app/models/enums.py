"""String enums. Stored as plain strings in Postgres (simpler migrations); validated in code."""
from enum import StrEnum


class Province(StrEnum):
    SINDH = "SINDH"
    KP = "KP"
    PUNJAB = "PUNJAB"
    BALOCHISTAN = "BALOCHISTAN"
    GB = "GB"
    AJK = "AJK"
    ICT = "ICT"


class FloodMode(StrEnum):
    SLOW_ONSET = "SLOW_ONSET"
    FLASH = "FLASH"


class DataSource(StrEnum):
    NASA_POWER = "NASA_POWER"
    GPM_IMERG = "GPM_IMERG"
    FORECAST_RAIN = "FORECAST_RAIN"
    SENTINEL1 = "SENTINEL1"
    WORLD_BANK = "WORLD_BANK"
    EASYDATA = "EASYDATA"


class Role(StrEnum):
    PDMA_OFFICER = "PDMA_OFFICER"
    DC = "DC"
    DONOR = "DONOR"
    ADMIN = "ADMIN"
