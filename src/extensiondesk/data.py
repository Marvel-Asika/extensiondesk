"""Loading the desk's data, with the cleaning and data-quality checks made explicit.

Nothing here silently fixes a record. Every repair or exclusion is reported back
as a data-quality issue so the officer can see it next to the advisory.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import config

CROP_ALIASES = {"maise": "maize", "corn": "maize", "guinea corn": "sorghum", "beans": "cowpea", "soya": "soybean"}
ACRE_TO_HA = 0.404686


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def parse_date(text: str, year_hint: int = 2025) -> date | None:
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_size_ha(text: str) -> tuple[float | None, str | None]:
    m = re.match(r"\s*([\d.]+)\s*(ha|hectares?|acres?|ac)\s*$", text or "", re.I)
    if not m:
        return None, f"plot size '{text}' could not be read"
    value, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith("ac"):
        return round(value * ACRE_TO_HA, 2), f"plot size given in acres ({text}); converted to hectares"
    return value, None


@dataclass
class Household:
    household_id: str
    farmer_id: str
    cluster_id: str
    crop: str
    plot_ha: float | None
    planned_planting: date | None
    has_gps: bool
    phone: str
    language: str
    issues: list[str] = field(default_factory=list)
    duplicate_of: str | None = None


def clusters() -> dict[str, dict]:
    return {r["cluster_id"]: r for r in _read(config.data_dir() / "clusters.csv")}


def crop_calendar() -> dict[str, dict]:
    return {r["crop"]: r for r in _read(config.data_dir() / "crop_calendar.csv")}


def pest_alerts() -> list[dict]:
    return _read(config.data_dir() / "pest_alerts.csv")


def households(cluster_id: str | None = None) -> list[Household]:
    """Load the register and annotate every row with its data-quality issues."""
    rows = _read(config.data_dir() / "households.csv")
    known_crops = set(crop_calendar())
    out: list[Household] = []
    first_by_farmer: dict[str, str] = {}
    for r in rows:
        issues: list[str] = []
        crop_raw = (r.get("crop") or "").strip().lower()
        crop = CROP_ALIASES.get(crop_raw, crop_raw)
        if crop != crop_raw:
            issues.append(f"crop recorded as '{r['crop']}', read as '{crop}'")
        if crop not in known_crops:
            issues.append(f"crop '{crop}' has no planting calendar")
        size, size_issue = parse_size_ha(r.get("plot_size", ""))
        if size_issue:
            issues.append(size_issue)
        planned = parse_date(r.get("planned_planting", ""))
        if planned is None:
            issues.append(f"planned planting date '{r.get('planned_planting')}' could not be read")
        has_gps = bool((r.get("plot_lat") or "").strip() and (r.get("plot_lon") or "").strip())
        if not has_gps:
            issues.append("no GPS point for the plot; zone taken from the cluster instead")
        hh = Household(
            household_id=r["household_id"], farmer_id=r["farmer_id"], cluster_id=r["cluster_id"],
            crop=crop, plot_ha=size, planned_planting=planned, has_gps=has_gps,
            phone=r.get("phone", ""), language=r.get("language", "ha"), issues=issues,
        )
        if hh.farmer_id in first_by_farmer:
            hh.duplicate_of = first_by_farmer[hh.farmer_id]
            hh.issues.append(f"farmer_id {hh.farmer_id} already registered as {hh.duplicate_of}; "
                             "excluded from SMS recipients until the officer resolves it")
        else:
            first_by_farmer[hh.farmer_id] = hh.household_id
        out.append(hh)
    if cluster_id:
        out = [h for h in out if h.cluster_id == cluster_id]
    return out
