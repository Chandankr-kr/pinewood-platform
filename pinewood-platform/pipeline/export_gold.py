"""Export the Gold layer to CSV so Power BI can connect without a DuckDB ODBC
driver. Writes one CSV per Gold table/view into powerbi/data/.

    python -m pipeline.export_gold
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from .common import ROOT, load_config

# Tables/views the Power BI model consumes.
EXPORTS = [
    "dim_community", "dim_date", "dim_care_level", "dim_unit", "dim_resident",
    "dim_resident_care_scd2",
    "fact_occupancy_monthly", "fact_census_monthly", "fact_labor_monthly",
    "fact_incidents", "fact_moveouts", "fact_reviews", "fact_leads",
    "fact_revenue_monthly", "fact_resident_acuity_monthly",
]


def main():
    cfg = load_config()
    out = ROOT / "powerbi" / "data"
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(cfg["warehouse"], read_only=True)
    for name in EXPORTS:
        target = (out / f"{name}.csv").as_posix()
        con.execute(f"COPY (SELECT * FROM gold.{name}) TO '{target}' (HEADER, DELIMITER ',')")
        n = con.execute(f"SELECT COUNT(*) FROM gold.{name}").fetchone()[0]
        print(f"  {name:32s} {n:>6} rows -> powerbi/data/{name}.csv")
    con.close()
    print(f"\nExported {len(EXPORTS)} tables to {out}")


if __name__ == "__main__":
    main()
