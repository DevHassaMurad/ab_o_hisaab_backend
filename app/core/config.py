"""Application settings (pydantic-settings). Every tunable lives here or in data/seeds (Principle IX)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- foundation ---
    DATABASE_URL: str = "postgresql+psycopg://aab:aab@localhost:5432/aab"
    JWT_SECRET: str = "change-me-in-env"
    JWT_TTL_MIN: int = 60
    SCHEDULER_ENABLED: bool = False
    PIPELINE_CRON: str = "0 1 * * *"
    FLAGS: str = ""

    # --- FR1 NASA POWER ---
    NASA_POWER_BASE_URL: str = "https://power.larc.nasa.gov/api/temporal/daily/point"
    POWER_LOOKBACK_DAYS: int = 10
    POWER_EXPECTED_LAG_DAYS: int = 3
    POWER_MAX_COPY_FORWARD_DAYS: int = 3
    POWER_MAX_WORKERS: int = 4
    POWER_TIMEOUT_S: float = 60.0
    POWER_MAX_RETRIES: int = 3
    POWER_RETRY_BASE_S: float = 2.0
    POWER_RAIN_MAX_MM: float = 600.0
    REPLAY_POWER_LAG_DAYS: int = 2
    REPLAY_SPINUP_DAYS: int = 30
    REPLAY_CACHE_ROOT: str = "data/replay"
    REPLAY_DEFINITIONS: str = "data/replay/scenarios.yaml"
    REPLAY_PIPELINE_TIME_UTC: str = "01:00"
    REPLAY_MAX_STEP_DAYS: int = 60
    REPLAY_AUTOPLAY_MIN_INTERVAL_S: int = 2
    VULNERABILITY_WEIGHTS: dict[str, float] = Field(
        default_factory=lambda: {"poverty_headcount": 0.5, "housing_kutcha_pct": 0.3, "literacy_rate": 0.2}
    )
    VULNERABILITY_INVERTED: list[str] = Field(default_factory=lambda: ["literacy_rate"])
    CONTEXT_INDICATORS_FILE: str = "data/seeds/context_indicators.csv"
    CONTEXT_REFRESH_CRON: str = "0 2 1 * *"
    WORLD_BANK_BASE_URL: str = "https://api.worldbank.org/v2"
    WORLD_BANK_POVERTY_CODE: str = "SI.POV.NAHC"

    # --- FR2 Earth Engine ---
    GEE_SERVICE_ACCOUNT: str = ""
    GEE_KEY_FILE: str = "secrets/gee-key.json"
    GEE_PROJECT: str = ""
    GEE_HIGH_VOLUME: bool = True
    IMERG_COLLECTION: str = "NASA/GPM_L3/IMERG_V07"
    IMERG_BAND: str = "precipitation"
    IMERG_REDUCE_SCALE_M: int = 5000
    IMERG_LOOKBACK_DAYS: int = 3
    IMERG_EXPECTED_LAG_DAYS: int = 2
    IMERG_MIN_IMAGES_PARTIAL: int = 40
    IMERG_MAX_COPY_FORWARD_DAYS: int = 3
    REPLAY_IMERG_LAG_DAYS: int = 1
    EE_TEHSIL_BATCH: int = 300
    EE_SIMPLIFY_DEG: float = 0.002
    S1_COLLECTION: str = "COPERNICUS/S1_GRD"
    S1_DIFF_DB: float = -3.0
    S1_AFTER_MAX_DB: float = -15.0
    S1_SPECKLE_RADIUS_M: int = 50
    JRC_COLLECTION: str = "JRC/GSW1_4/GlobalSurfaceWater"
    JRC_PERM_OCCURRENCE: int = 80
    SRTM_COLLECTION: str = "USGS/SRTMGL1_003"
    S1_MAX_SLOPE_DEG: float = 5.0
    S1_MIN_CONNECTED_PX: int = 8
    S1_AREA_SCALE_M: int = 30
    THUMB_DIMENSIONS: int = 1024

    # --- FR3 forecasting / FR4 modes ---
    OPEN_METEO_BASE: str = "https://api.open-meteo.com"
    OPEN_METEO_MODEL: str = "best_match"
    OPEN_METEO_API_KEY: str = ""
    FORECAST_HTTP_TIMEOUT_S: float = 20.0
    FORECAST_HTTP_RETRIES: int = 2
    FORECAST_BATCH_SIZE: int = 50
    FORECAST_ARTIFACT_DIR: str = "ml/artifacts"
    FORECAST_MODES_DIR: str = "ml/config/modes"
    FORECAST_DISTRICT_MIN_POP_SHARE: float = 0.10
    FORECAST_DEFAULT_THRESHOLDS: str = "40,60,80"
    FORECAST_MAX_MISSING_WINDOW_DAYS: int = 2
    FLOOD_MODE_SEED: str = "data/seeds/flood_modes.yaml"

    def flag(self, name: str) -> bool:
        return name in {f.strip() for f in self.FLAGS.split(",") if f.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
