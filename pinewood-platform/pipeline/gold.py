"""Gold layer: star schema the API and Power BI consume directly.

Dimensions: dim_community, dim_date, dim_care_level, dim_unit, dim_resident,
            dim_resident_care_scd2 (Type 2 on resident care level).
Facts:      fact_occupancy_monthly, fact_census_monthly, fact_labor_monthly,
            fact_incidents, fact_moveouts, fact_reviews, fact_leads.

Grain is documented per fact table in sql/gold_ddl.sql and the README.
The reference window is the 6 months present in the data (2025-01..2025-06).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .common import ROOT

from .common import CARE_LEVEL_CODE

PERIOD_START = date(2025, 1, 1)
PERIOD_END = date(2025, 6, 30)


def _months(start: date, end: date) -> list[date]:
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _month_end(first: date) -> date:
    y, m = (first.year + 1, 1) if first.month == 12 else (first.year, first.month + 1)
    return date(y, m, 1) - timedelta(days=1)


def _overlap_days(a_start, a_end, b_start, b_end) -> int:
    if a_start is None:
        return 0
    a_end = a_end or b_end
    lo, hi = max(a_start, b_start), min(a_end, b_end)
    return (hi - lo).days + 1 if hi >= lo else 0


def _sdf(con, table: str) -> pd.DataFrame:
    return con.execute(f"SELECT * FROM silver.{table}").df()


def _to_date(series: pd.Series) -> pd.Series:
    s = pd.to_datetime(series, errors="coerce")
    return s.dt.date.where(s.notna(), None)


def _write(con, table: str, df: pd.DataFrame):
    con.register("g_df", df)
    con.execute(f"DROP TABLE IF EXISTS gold.{table}")
    con.execute(f"CREATE TABLE gold.{table} AS SELECT * FROM g_df")
    con.unregister("g_df")


def _build_scd2(residents: pd.DataFrame, care: pd.DataFrame) -> pd.DataFrame:
    """Type-2 dimension for resident care level.

    A resident moving Assisted Living -> Memory Care yields two rows with
    non-overlapping effective windows, not an overwrite.
    """
    rows: list[dict] = []
    care = care.sort_values(["resident_id", "change_date"])
    care_by_res = {rid: g for rid, g in care.groupby("resident_id")}

    def _is_date(x):
        return isinstance(x, date)

    def _clean_level(x):
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        x = str(x).strip()
        return x if x in ("Independent Living", "Assisted Living", "Memory Care") else None

    for _, r in residents.iterrows():
        rid = r["resident_id"]
        admit = r["admit_date"] if _is_date(r["admit_date"]) else None
        discharge = r["discharge_date"] if _is_date(r["discharge_date"]) else None
        events = care_by_res.get(rid)

        segments: list[tuple] = []  # (effective_date, care_level)
        if events is not None and len(events):
            first = events.iloc[0]
            first_change = first["change_date"] if _is_date(first["change_date"]) else None
            # Seed the opening segment from admit + the level prior to first change.
            seed_level = _clean_level(first["previous_level"]) or _clean_level(r["care_level"])
            seed_date = admit
            # Only add a seed period if it is a real, non-empty interval before
            # the first recorded change (otherwise the change row covers it).
            if seed_level and seed_date is not None and (
                first_change is None or seed_date < first_change
            ):
                segments.append((seed_date, seed_level))
            for _, e in events.iterrows():
                lvl = _clean_level(e["new_level"])
                if lvl and _is_date(e["change_date"]):
                    segments.append((e["change_date"], lvl))
        else:
            lvl0 = _clean_level(r["care_level"])
            if lvl0 and admit:
                segments.append((admit, lvl0))

        # Collapse consecutive identical levels and order by date.
        segments = sorted(
            [s for s in segments if _is_date(s[0])], key=lambda s: s[0]
        )
        cleaned: list[tuple] = []
        for d, lvl in segments:
            if cleaned and cleaned[-1][1] == lvl:
                continue
            cleaned.append((d, lvl))

        for i, (eff, lvl) in enumerate(cleaned):
            end = (cleaned[i + 1][0] - timedelta(days=1)) if i + 1 < len(cleaned) else discharge
            is_current = (i == len(cleaned) - 1) and (discharge is None)
            rows.append({
                "resident_id": rid,
                "community_id": r["community_id"],
                "care_level": lvl,
                "effective_date": eff,
                "end_date": end,
                "is_current": bool(is_current),
            })

    scd = pd.DataFrame(rows)
    if not scd.empty:
        scd.insert(0, "care_sk", range(1, len(scd) + 1))
    return scd


def _care_level_on(scd_by_res: dict, rid: str, when) -> str | None:
    segs = scd_by_res.get(rid)
    if segs is None or when is None:
        return None
    for eff, end, lvl in segs:
        if eff <= when and (end is None or when <= end):
            return lvl
    return None


def build_gold(con, cfg: dict, run_log: dict | None = None) -> dict:
    stats: dict[str, dict] = {}
    months = _months(PERIOD_START, PERIOD_END)

    communities = _sdf(con, "communities")
    residents = _sdf(con, "residents")
    care = _sdf(con, "care_history")
    incidents = _sdf(con, "incidents")
    units = _sdf(con, "units")
    leases = _sdf(con, "leases")
    shifts = _sdf(con, "shifts")
    reviews = _sdf(con, "reviews")
    leads = _sdf(con, "leads")

    for df, cols in [
        (residents, ["admit_date", "discharge_date", "dob"]),
        (care, ["change_date"]),
        (incidents, ["incident_date"]),
        (units, ["snapshot_date"]),
        (leases, ["move_in_date", "move_out_date"]),
        (shifts, ["shift_date"]),
        (reviews, ["review_date"]),
        (leads, ["created_date", "tour_date", "deposit_date", "move_in_date"]),
    ]:
        for c in cols:
            df[c] = _to_date(df[c])

    # ---------------- dim_community ---------------- #
    _write(con, "dim_community", communities)

    # ---------------- dim_date ---------------- #
    dd = []
    d = PERIOD_START
    while d <= PERIOD_END:
        dd.append({
            "date_key": int(d.strftime("%Y%m%d")), "date": d, "year": d.year,
            "month": d.month, "month_name": d.strftime("%B"),
            "quarter": (d.month - 1) // 3 + 1, "day": d.day,
            "month_start": date(d.year, d.month, 1),
        })
        d += timedelta(days=1)
    _write(con, "dim_date", pd.DataFrame(dd))

    # ---------------- dim_care_level ---------------- #
    _write(con, "dim_care_level", pd.DataFrame([
        {"care_level": k, "care_code": v} for k, v in CARE_LEVEL_CODE.items()
    ]))

    # ---------------- dim_unit ---------------- #
    u_latest = units.sort_values("snapshot_date").drop_duplicates(
        "unit_id", keep="last"
    ).reset_index(drop=True)
    u_latest.insert(0, "unit_key", range(1, len(u_latest) + 1))
    _write(con, "dim_unit", u_latest[[
        "unit_key", "unit_id", "community_id", "unit_type", "care_level", "monthly_rent",
    ]])

    # ---------------- dim_resident ---------------- #
    dim_res = residents.copy()
    dim_res.insert(0, "resident_key", range(1, len(dim_res) + 1))
    dim_res = dim_res.rename(columns={"care_level": "current_care_level"})
    _write(con, "dim_resident", dim_res[[
        "resident_key", "resident_id", "community_id", "first_name", "last_name",
        "dob", "gender", "admit_date", "discharge_date", "current_care_level",
    ]])

    # ---------------- dim_resident_care_scd2 ---------------- #
    scd = _build_scd2(residents, care)
    _write(con, "dim_resident_care_scd2", scd)
    scd_by_res: dict[str, list] = {}
    if not scd.empty:
        for rid, g in scd.groupby("resident_id"):
            scd_by_res[rid] = list(
                zip(g["effective_date"], g["end_date"], g["care_level"])
            )
    stats["dim_resident_care_scd2"] = {"rows": len(scd)}

    # ---------------- fact_census_monthly ---------------- #
    census_rows: list[dict] = []
    for m in months:
        m_end = _month_end(m)
        for _, r in residents.iterrows():
            days = _overlap_days(r["admit_date"], r["discharge_date"], m, m_end)
            if days <= 0:
                continue
            lvl = _care_level_on(scd_by_res, r["resident_id"], m_end) or r["care_level"]
            census_rows.append({
                "community_id": r["community_id"], "care_level": lvl,
                "month_start": m, "resident_id": r["resident_id"], "resident_days": days,
            })
    census = pd.DataFrame(census_rows)
    census_agg = (
        census.groupby(["community_id", "care_level", "month_start"])
        .agg(resident_days=("resident_days", "sum"),
             distinct_residents=("resident_id", "nunique"))
        .reset_index()
    )
    census_agg["days_in_month"] = census_agg["month_start"].map(
        lambda x: (_month_end(x) - x).days + 1
    )
    census_agg["avg_daily_census"] = (
        census_agg["resident_days"] / census_agg["days_in_month"]
    ).round(2)
    _write(con, "fact_census_monthly", census_agg)
    stats["fact_census_monthly"] = {"rows": len(census_agg)}

    # ---------------- fact_occupancy_monthly ---------------- #
    # Occupancy = residents physically present (from census) over licensed unit
    # capacity. NOTE: Yardi leases are a TRANSACTIONAL export (a lease only
    # appears in the month it is created or moves out), not a full snapshot of
    # active leases, so counting "active leases" would massively undercount
    # occupancy. PCC residents IS a monthly active-resident snapshot, so we use
    # average daily census as the occupied figure — the trustworthy source.
    census_present = (
        census.groupby(["community_id", "month_start"])["resident_id"]
        .nunique().reset_index(name="residents_present")
    )
    occ_rows: list[dict] = []
    for m in months:
        # available units: snapshot for the month, else latest known.
        snap = units[units["snapshot_date"] == m]
        if snap.empty:
            snap = u_latest
        units_by_comm = snap.groupby("community_id")["unit_id"].nunique()
        present = census_present[census_present["month_start"] == m]
        present_by_comm = present.set_index("community_id")["residents_present"]
        for comm in communities["community_id"]:
            total = int(units_by_comm.get(comm, 0))
            occ = int(present_by_comm.get(comm, 0))
            occ_rows.append({
                "community_id": comm, "month_start": m,
                "total_units": total, "occupied_units": occ,
                "occupancy_rate": round(occ / total, 4) if total else None,
            })
    _write(con, "fact_occupancy_monthly", pd.DataFrame(occ_rows))
    stats["fact_occupancy_monthly"] = {"rows": len(occ_rows)}

    # ---------------- fact_revenue_monthly ---------------- #
    # Revenue = monthly_rate prorated by the days each lease was occupied in
    # the month. Grain: one row per community per month.
    rev_rows: list[dict] = []
    for m in months:
        m_end = _month_end(m)
        dim = (m_end - m).days + 1
        for _, l in leases.iterrows():
            if l["move_in_date"] is None or pd.isna(l["monthly_rate"]):
                continue
            days = _overlap_days(l["move_in_date"], l["move_out_date"], m, m_end)
            if days <= 0:
                continue
            rev_rows.append({
                "community_id": l["community_id"], "month_start": m,
                "revenue": float(l["monthly_rate"]) * days / dim,
            })
    rev = pd.DataFrame(rev_rows)
    rev = (rev.groupby(["community_id", "month_start"])["revenue"].sum()
           .round(2).reset_index()) if not rev.empty else pd.DataFrame(
        columns=["community_id", "month_start", "revenue"])
    _write(con, "fact_revenue_monthly", rev)
    stats["fact_revenue_monthly"] = {"rows": len(rev)}

    # ---------------- fact_labor_monthly ---------------- #
    shifts = shifts[shifts["shift_date"].notna()].copy()
    shifts["month_start"] = shifts["shift_date"].map(lambda x: date(x.year, x.month, 1))
    labor = (
        shifts.groupby(["community_id", "month_start", "role"])
        .agg(shift_count=("shift_id", "count"),
             hours_worked=("hours_worked", "sum"),
             labor_cost=("labor_cost", "sum"))
        .reset_index()
    )
    _write(con, "fact_labor_monthly", labor)
    stats["fact_labor_monthly"] = {"rows": len(labor)}

    # ---------------- fact_incidents ---------------- #
    inc = incidents[incidents["incident_date"].notna()].copy()
    inc["month_start"] = inc["incident_date"].map(lambda x: date(x.year, x.month, 1))
    inc["care_level"] = [
        _care_level_on(scd_by_res, rid, d)
        for rid, d in zip(inc["resident_id"], inc["incident_date"])
    ]
    _write(con, "fact_incidents", inc[[
        "incident_id", "resident_id", "community_id", "incident_date", "month_start",
        "incident_type", "severity", "care_level", "reported_by",
    ]])
    stats["fact_incidents"] = {"rows": len(inc)}

    # ---------------- fact_moveouts ---------------- #
    mo = leases[leases["move_out_date"].notna()].copy()
    mo["los_days"] = [
        (out_d - in_d).days if (in_d is not None and out_d is not None) else None
        for in_d, out_d in zip(mo["move_in_date"], mo["move_out_date"])
    ]
    mo["move_out_month"] = mo["move_out_date"].map(lambda x: date(x.year, x.month, 1))
    mo["care_level"] = [
        _care_level_on(scd_by_res, rid, d) for rid, d in zip(mo["resident_id"], mo["move_out_date"])
    ]
    _write(con, "fact_moveouts", mo[[
        "lease_id", "resident_id", "community_id", "unit_id", "move_in_date",
        "move_out_date", "move_out_month", "move_out_reason", "los_days", "care_level",
    ]])
    stats["fact_moveouts"] = {"rows": len(mo)}

    # ---------------- fact_reviews ---------------- #
    rv = reviews[reviews["review_date"].notna()].copy()
    rv["month_start"] = rv["review_date"].map(lambda x: date(x.year, x.month, 1))
    rv["has_response"] = rv["response_text"].notna() & (rv["response_text"] != "")
    _write(con, "fact_reviews", rv[[
        "review_id", "community_id", "review_date", "month_start", "rating",
        "has_response",
    ]])
    stats["fact_reviews"] = {"rows": len(rv)}

    # ---------------- fact_leads ---------------- #
    ld = leads.copy()
    ld["created_month"] = ld["created_date"].map(
        lambda x: date(x.year, x.month, 1) if x is not None else None
    )
    ld["toured"] = ld["tour_date"].notna()
    ld["deposited"] = ld["deposit_date"].notna()
    _write(con, "fact_leads", ld[[
        "lead_id", "community_id", "lead_source", "created_date", "created_month",
        "tour_date", "deposit_date", "move_in_date", "status", "lost_reason",
        "toured", "deposited",
    ]])
    stats["fact_leads"] = {"rows": len(ld)}

    # ---------------- fact_resident_acuity_monthly ---------------- #
    # Monthly acuity time series, rebuilt from the Bronze monthly snapshots
    # (Silver keeps only the latest snapshot). Drives the acuity-escalation view.
    from .common import clean_acuity
    bronze_res = con.execute(
        "SELECT resident_id, community_id, acuity_score, _source_month "
        "FROM bronze.pcc_residents"
    ).df()
    acu_rows = []
    for _, r in bronze_res.iterrows():
        score, valid = clean_acuity(r["acuity_score"])
        if not valid:
            continue
        ym = str(r["_source_month"])  # 'YYYY-MM'
        try:
            y, mo_ = int(ym[:4]), int(ym[5:7])
        except ValueError:
            continue
        acu_rows.append({
            "resident_id": r["resident_id"], "community_id": r["community_id"],
            "month_start": date(y, mo_, 1), "acuity_score": score,
        })
    acu_df = pd.DataFrame(acu_rows).drop_duplicates(["resident_id", "month_start"])
    _write(con, "fact_resident_acuity_monthly", acu_df)
    stats["fact_resident_acuity_monthly"] = {"rows": len(acu_df)}

    # ---------------- analytics views ---------------- #
    views_sql = ROOT / "sql" / "gold_views.sql"
    if views_sql.exists():
        con.execute(views_sql.read_text(encoding="utf-8"))
        stats["analytics_views"] = {"created": "sql/gold_views.sql"}

    if run_log is not None:
        run_log["gold"] = stats
    return stats
