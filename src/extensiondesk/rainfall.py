"""Rainfall signal from CHIRPS daily data, judged against rules.yaml.

The numbers are computed here, deterministically. The language model never does
arithmetic on rainfall; it reads the result and the rule verdict and cites it.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, timedelta

from . import config

CHIRPS_CITATION = ("CHIRPS v2.0 daily rainfall (UCSB Climate Hazards Center), area mean for zone {zone}, "
                   "retrieved via SERVIR ClimateSERV; normal = 2010-2024 dekadal mean")


def _load_daily() -> dict[str, dict[date, float | None]]:
    path = config.data_dir() / "rainfall" / "chirps_daily_2025.csv"
    series: dict[str, dict[date, float | None]] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = date.fromisoformat(row["date"])
            for zone, v in row.items():
                if zone == "date":
                    continue
                series.setdefault(zone, {})[d] = float(v) if v not in ("", None) else None
    # mask documented feed outages (simulated, see feed_outages.csv)
    out_path = config.data_dir() / "rainfall" / "feed_outages.csv"
    if out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                z = row["zone_id"]
                d, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
                while d <= end:
                    if z in series and d in series[z]:
                        series[z][d] = None
                    d += timedelta(days=1)
    return series


def _load_normals() -> dict[str, dict[tuple[int, int], float]]:
    path = config.data_dir() / "rainfall" / "chirps_dekadal_climatology_2010_2024.csv"
    out: dict[str, dict[tuple[int, int], float]] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m, dk = int(row["month"]), int(row["dekad"])
            for zone, v in row.items():
                if zone in ("month", "dekad"):
                    continue
                out.setdefault(zone, {})[(m, dk)] = float(v)
    return out


def _normal_for_day(normals: dict[tuple[int, int], float], d: date) -> float | None:
    dk = 1 if d.day <= 10 else 2 if d.day <= 20 else 3
    total = normals.get((d.month, dk))
    if total is None:
        return None
    length = 10 if dk < 3 else calendar.monthrange(d.year, d.month)[1] - 20
    return total / length


def available_zones() -> list[str]:
    return sorted(_load_daily().keys())


def signal(zone: str, as_of: date) -> dict:
    """Compute the rainfall signal for a zone on a date and apply the rules."""
    rules = config.rules()
    daily_all = _load_daily()
    if zone not in daily_all:
        return {"zone": zone, "available": False,
                "reason": f"No rainfall feed exists for zone {zone}. Advice cannot be based on rainfall here."}
    daily = daily_all[zone]
    first, last = min(daily), max(daily)
    if not (first <= as_of <= last):
        return {"zone": zone, "available": False,
                "reason": f"No data for {as_of}. The feed covers {first} to {last}."}

    rain_day = rules["rain_day_mm"]
    win = rules["anomaly"]["window_days"]
    window = [as_of - timedelta(days=i) for i in range(win - 1, -1, -1)]
    window = [d for d in window if d >= first]
    missing = [d for d in window if daily.get(d) is None]
    vals = {d: daily.get(d) for d in window}

    def total(days: list[date]) -> float:
        return round(sum(vals[d] or 0.0 for d in days), 1)

    total_10 = total(window[-10:])
    total_30 = total(window)
    normals = _load_normals().get(zone, {})
    normal_30 = round(sum(_normal_for_day(normals, d) or 0.0 for d in window), 1)
    anomaly_pct = round(100 * (total_30 - normal_30) / normal_30, 0) if normal_30 else None

    # current dry spell (consecutive dry days ending on as_of; missing days break the count)
    current_dry = 0
    for d in reversed(window):
        v = vals[d]
        if v is None or v >= rain_day:
            break
        current_dry += 1

    # onset: first window_days run with >= min_total since season start, not followed by
    # a dry spell longer than max_dry_spell_days (up to as_of)
    o = rules["onset"]
    m, dd = (int(x) for x in rules["season_start"].split("-"))
    season_start = max(date(as_of.year, m, dd), first)
    days = []
    d = season_start
    while d <= as_of:
        days.append(d)
        d += timedelta(days=1)
    onset_date, false_starts = None, []
    i = 0
    while i + o["window_days"] <= len(days):
        run = days[i:i + o["window_days"]]
        if any(daily.get(x) is None for x in run):
            i += 1
            continue
        if sum(daily[x] for x in run) >= o["min_total_mm"]:
            cand = run[-1]
            # longest dry spell after the candidate
            longest, cur = 0, 0
            for x in days[days.index(cand) + 1:]:
                v = daily.get(x)
                if v is not None and v < rain_day:
                    cur += 1
                    longest = max(longest, cur)
                else:
                    cur = 0
            if longest > o["max_dry_spell_days"]:
                false_starts.append(cand.isoformat())
                i += 1
                continue
            onset_date = cand
            break
        i += 1

    # verdict
    max_missing = rules["data_gap"]["max_missing_days"]
    if len(missing) > max_missing:
        verdict = "insufficient_data"
        why = f"{len(missing)} of the last {len(window)} days have no rainfall data (limit {max_missing})."
    elif current_dry >= rules["hold"]["current_dry_spell_days"]:
        verdict = "hold"
        why = f"Dry for {current_dry} days in a row up to {as_of} (hold threshold {rules['hold']['current_dry_spell_days']})."
    elif onset_date:
        verdict = "plant_ok"
        why = (f"Onset established on {onset_date}: {o['window_days']}-day rain reached {o['min_total_mm']} mm "
               f"with no dry spell over {o['max_dry_spell_days']} days since.")
    else:
        verdict = "hold"
        why = "Onset of rains not yet established under the rules."

    start = window[0]
    return {
        "zone": zone,
        "available": True,
        "as_of": as_of.isoformat(),
        "window": f"{start.isoformat()}..{as_of.isoformat()}",
        "rain_10d_mm": total_10,
        "rain_30d_mm": total_30,
        "normal_30d_mm": normal_30,
        "anomaly_30d_pct": anomaly_pct,
        "current_dry_spell_days": current_dry,
        "missing_days": [x.isoformat() for x in missing],
        "onset_date": onset_date.isoformat() if onset_date else None,
        "false_starts": false_starts,
        "rule_verdict": verdict,
        "rule_reason": why,
        "citation": CHIRPS_CITATION.format(zone=zone) + f"; window {start}..{as_of}",
    }
