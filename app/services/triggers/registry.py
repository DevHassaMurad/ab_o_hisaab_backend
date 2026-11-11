"""FR6 hooks into FR3: score-to-tier mapping uses each district's configured thresholds."""
from app.services.forecasting.service import THRESHOLD_LOADERS
from app.services.triggers.tiers import thresholds_for_district


def register_fund_rules() -> None:
    if thresholds_for_district not in THRESHOLD_LOADERS:
        THRESHOLD_LOADERS.append(thresholds_for_district)
