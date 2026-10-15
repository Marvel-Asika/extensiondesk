"""Generate the synthetic household register and pest alerts.

Everything this script writes is SYNTHETIC. No real farmer, household or phone
number is used. The mess in the data is deliberate: real registers kept by a
small extension desk have duplicates, missing GPS points, mixed date formats and
unit mix-ups, and the agent has to cope with them instead of assuming them away.

Run:  uv run python scripts/make_synthetic_data.py
The output is deterministic (fixed seed), so re-running gives the same files.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEED = 2025

# cluster -> rainfall zone. C10 points at a zone that has no rainfall feed (Z6)
# on purpose, to test that the agent says "no data" instead of guessing.
CLUSTERS = {
    "C01": ("Z1", "Kurmi"), "C02": ("Z1", "Tashan Iya"), "C03": ("Z2", "Gidan Haki"),
    "C04": ("Z2", "Rafin Kada"), "C05": ("Z3", "Unguwar Dutse"), "C06": ("Z3", "Kofar Gabas"),
    "C07": ("Z4", "Dorayi"), "C08": ("Z4", "Bakin Kogi"), "C09": ("Z5", "Tudun Wada"),
    "C10": ("Z6", "Sabon Layi"),
}
ZONE_CENTROIDS = {"Z1": (11.17, 8.23), "Z2": (11.25, 8.15), "Z3": (11.10, 8.30),
                  "Z4": (11.22, 8.35), "Z5": (11.05, 8.17), "Z6": (11.30, 8.28)}
CROPS = [("maize", 0.45), ("sorghum", 0.30), ("soybean", 0.15), ("cowpea", 0.10)]
FIRST = ["Abubakar", "Aisha", "Bala", "Hauwa", "Ibrahim", "Zainab", "Musa", "Fatima",
         "Sani", "Hadiza", "Yusuf", "Maryam", "Usman", "Rakiya", "Danjuma", "Asabe"]
LAST = ["Ali", "Bello", "Garba", "Ibrahim", "Lawal", "Sule", "Yakubu", "Idris", "Umar", "Haruna"]


def pick_crop(rng: random.Random) -> str:
    r, acc = rng.random(), 0.0
    for crop, w in CROPS:
        acc += w
        if r <= acc:
            return crop
    return CROPS[-1][0]


def fmt_date(rng: random.Random, y: int, m: int, d: int) -> str:
    """Planned planting dates arrive in whatever format the clerk used."""
    style = rng.choice(["iso", "iso", "iso", "dmy_slash", "dmy_text"])
    if style == "iso":
        return f"{y:04d}-{m:02d}-{d:02d}"
    if style == "dmy_slash":
        return f"{d:02d}/{m:02d}/{y:04d}"
    month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"][m - 1]
    return f"{d} {month} {y}"


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    n = 0
    for cid, (zone, village) in CLUSTERS.items():
        for _ in range(5):
            n += 1
            crop = pick_crop(rng)
            lat0, lon0 = ZONE_CENTROIDS[zone]
            lat = round(lat0 + rng.uniform(-0.025, 0.025), 5)
            lon = round(lon0 + rng.uniform(-0.025, 0.025), 5)
            m, d = (6, rng.randint(1, 25)) if crop in ("maize", "sorghum") else (6, rng.randint(15, 30))
            if crop == "cowpea":
                m, d = 7, rng.randint(1, 20)
            size = round(rng.uniform(0.4, 2.5), 2)
            rows.append({
                "household_id": f"HH{n:03d}",
                "farmer_id": f"F{1000 + n}",
                "head_name": f"{rng.choice(FIRST)} {rng.choice(LAST)} (synthetic)",
                "cluster_id": cid,
                "village": village,
                "plot_lat": lat,
                "plot_lon": lon,
                "crop": crop,
                "plot_size": f"{size} ha",
                "planned_planting": fmt_date(rng, 2025, m, d),
                "phone": f"+234-000-000-{n:04d}",
                "language": rng.choice(["ha", "ha", "ha", "en"]),
            })

    # ---- deliberate mess -------------------------------------------------
    # 1. three households with no GPS point
    for hid in ("HH004", "HH017", "HH038"):
        r = next(x for x in rows if x["household_id"] == hid)
        r["plot_lat"], r["plot_lon"] = "", ""
    # 2. duplicate farmer IDs (the same farmer registered twice, once with a typo)
    dup = dict(next(x for x in rows if x["household_id"] == "HH012"))
    dup["household_id"] = "HH051"
    dup["head_name"] = dup["head_name"].replace("(synthetic)", " (synthetic)").strip()
    rows.append(dup)
    r = next(x for x in rows if x["household_id"] == "HH027")
    r["farmer_id"] = next(x for x in rows if x["household_id"] == "HH026")["farmer_id"]
    # 3. crop misspelled and plot size in acres
    next(x for x in rows if x["household_id"] == "HH008")["crop"] = "maise"
    next(x for x in rows if x["household_id"] == "HH021")["plot_size"] = "3 acres"
    # 4. a planned date that cannot be parsed
    next(x for x in rows if x["household_id"] == "HH033")["planned_planting"] = "after first rains"

    with (DATA / "households.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    alerts = [
        {"alert_id": "PA-2025-014", "zone_id": "Z2", "reported_on": "2025-06-16", "pest": "fall armyworm",
         "crop": "maize", "severity": "moderate", "detail": "Larvae in whorls on 3 of 10 scouted plots",
         "origin": "SYNTHETIC, modelled on a ward scouting report"},
        {"alert_id": "PA-2025-019", "zone_id": "Z4", "reported_on": "2025-07-08", "pest": "fall armyworm",
         "crop": "maize", "severity": "high", "detail": "Window-pane damage on 6 of 10 scouted plots",
         "origin": "SYNTHETIC, modelled on a ward scouting report"},
        {"alert_id": "PA-2025-021", "zone_id": "Z3", "reported_on": "2025-07-10", "pest": "stem borer",
         "crop": "sorghum", "severity": "low", "detail": "Dead hearts on 1 of 10 scouted plots",
         "origin": "SYNTHETIC, modelled on a ward scouting report"},
    ]
    with (DATA / "pest_alerts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(alerts[0].keys()))
        w.writeheader()
        w.writerows(alerts)
    print(f"wrote {len(rows)} households and {len(alerts)} pest alerts")


if __name__ == "__main__":
    main()
