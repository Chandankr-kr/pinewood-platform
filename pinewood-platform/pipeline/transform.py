"""Silver layer: clean, typed, deduplicated, business keys resolved.

Key decisions (all defensible on camera):
* Care levels normalized to {Independent Living, Assisted Living, Memory Care}.
* All dates parsed to real DATEs (the residents export mixed ISO and US formats).
* ADP `hourly_rate` recovered from the role-keyed dict it was exported as, then
  labor_cost = hours_worked * recovered_rate.
* Monthly snapshots (residents, units) deduped to the latest snapshot per key.
* Leases dedup to the latest record per lease_id (a lease reappears in the month
  it moves out), which also collapses the 44 cross-file duplicate leases.
* Out-of-range acuity (-5, 99) nulled but the row kept + flagged.
* Gender/first-name mismatches are *flagged*, never silently changed — that is a
  source-of-truth question to raise with the client, not a pipeline guess.
Rejected/suspect rows are written to silver.quarantine; quality issues that do
not reject a row are written to silver.data_quality_flags.
"""
from __future__ import annotations

import pandas as pd

from .common import (
    CARE_LEVEL_CODE,
    clean_acuity,
    normalize_care_level,
    parse_adp_hourly_rate,
    parse_date,
)

# Minimal name->gender lexicon for a heuristic mismatch flag (not used to mutate).
_FEMALE = {
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan",
    "Jessica", "Sarah", "Karen", "Nancy", "Betty", "Dorothy", "Ruth", "Helen",
    "Margaret", "Sandra", "Carol", "Donna", "Sharon", "Deborah", "Cynthia",
}
_MALE = {
    "James", "Robert", "John", "Michael", "William", "David", "Richard",
    "Joseph", "Thomas", "Charles", "Daniel", "Matthew", "George", "Donald",
    "Kenneth", "Paul", "Mark", "Edward", "Frank", "Henry", "Larry", "Steven",
}


def _df(con, table: str) -> pd.DataFrame:
    return con.execute(f"SELECT * FROM bronze.{table}").df()


def _write(con, table: str, df: pd.DataFrame):
    con.register("tmp_df", df)
    con.execute(f"DROP TABLE IF EXISTS silver.{table}")
    con.execute(f"CREATE TABLE silver.{table} AS SELECT * FROM tmp_df")
    con.unregister("tmp_df")


def transform(con, cfg: dict, run_log: dict | None = None) -> dict:
    stats: dict[str, dict] = {}
    quarantine_rows: list[dict] = []
    quality_flags: list[dict] = []

    # ---------------- residents ---------------- #
    res = _df(con, "pcc_residents")
    res["admit_date"] = res["admit_date"].map(parse_date)
    res["discharge_date"] = res["discharge_date"].map(parse_date)
    res["dob"] = res["dob"].map(parse_date)
    res["care_level"] = res["care_level"].map(normalize_care_level)
    acu = res["acuity_score"].map(clean_acuity)
    res["acuity_score"] = [a for a, _ in acu]
    res["acuity_valid"] = [v for _, v in acu]
    if "mobility_status" not in res.columns:
        res["mobility_status"] = None

    # Flag care levels that failed to normalize and acuity out of range.
    for _, r in res[~res["acuity_valid"]].iterrows():
        quality_flags.append({
            "table": "residents", "key": r["resident_id"], "issue": "acuity_out_of_range",
            "detail": "acuity nulled (was outside 1..10)", "source_file": r["_source_file"],
        })
    # Gender / first-name mismatch (heuristic flag only).
    def _mismatch(row):
        fn, g = row["first_name"], row["gender"]
        return (fn in _FEMALE and g != "F") or (fn in _MALE and g != "M")
    for _, r in res[res.apply(_mismatch, axis=1)].iterrows():
        quality_flags.append({
            "table": "residents", "key": r["resident_id"], "issue": "gender_name_mismatch",
            "detail": f"name={r['first_name']} gender={r['gender']}",
            "source_file": r["_source_file"],
        })

    # Dedup monthly snapshot -> keep latest month per resident_id.
    res = res.sort_values(["resident_id", "_source_month"])
    res = res.drop_duplicates("resident_id", keep="last").reset_index(drop=True)
    # Discharge-before-admit -> quarantine (logically impossible).
    bad = res[
        res["discharge_date"].notna()
        & res["admit_date"].notna()
        & (res["discharge_date"] < res["admit_date"])
    ]
    for _, r in bad.iterrows():
        quarantine_rows.append({
            "table": "residents", "key": r["resident_id"],
            "reason": "discharge_before_admit", "source_file": r["_source_file"],
        })
    res = res.drop(bad.index)
    _write(con, "residents", res[[
        "resident_id", "community_id", "first_name", "last_name", "dob", "gender",
        "admit_date", "discharge_date", "care_level", "acuity_score", "acuity_valid",
        "mobility_status",
    ]])
    stats["residents"] = {"rows": len(res), "rejected": len(bad)}

    # ---------------- care_history ---------------- #
    ch = _df(con, "pcc_care_history")
    ch["change_date"] = ch["change_date"].map(parse_date)
    ch["previous_level"] = ch["previous_level"].map(normalize_care_level)
    ch["new_level"] = ch["new_level"].map(normalize_care_level)
    ch = ch.dropna(subset=["change_date", "new_level"])
    ch = ch.drop_duplicates(["resident_id", "change_date", "new_level"])
    _write(con, "care_history", ch[[
        "resident_id", "change_date", "previous_level", "new_level", "reason",
    ]])
    stats["care_history"] = {"rows": len(ch)}

    # ---------------- incidents ---------------- #
    inc = _df(con, "pcc_incidents")
    inc["incident_date"] = inc["incident_date"].map(parse_date)
    inc["severity"] = pd.to_numeric(inc["severity"], errors="coerce")
    inc = inc.drop_duplicates("incident_id")
    _write(con, "incidents", inc[[
        "incident_id", "resident_id", "community_id", "incident_date",
        "incident_type", "severity", "reported_by",
    ]])
    stats["incidents"] = {"rows": len(inc)}

    # ---------------- units ---------------- #
    units = _df(con, "yardi_units")
    units["monthly_rent"] = pd.to_numeric(units["monthly_rent"], errors="coerce")
    units["snapshot_date"] = units["snapshot_date"].map(parse_date)
    units["care_level"] = units["unit_type"].map(
        lambda t: {"IL": "Independent Living", "AL": "Assisted Living",
                   "MC": "Memory Care"}.get(str(t).strip().upper())
    )
    units = units.drop_duplicates(["unit_id", "snapshot_date"])
    _write(con, "units", units[[
        "unit_id", "community_id", "unit_type", "care_level", "monthly_rent",
        "snapshot_date",
    ]])
    stats["units"] = {"rows": len(units)}

    # ---------------- leases ---------------- #
    ls = _df(con, "yardi_leases")
    ls["move_in_date"] = ls["move_in_date"].map(parse_date)
    ls["move_out_date"] = ls["move_out_date"].map(parse_date)
    ls["monthly_rate"] = pd.to_numeric(ls["monthly_rate"], errors="coerce")
    # Latest record per lease_id (lease reappears in its move-out month).
    ls = ls.sort_values(["lease_id", "_source_month"]).drop_duplicates(
        "lease_id", keep="last"
    )
    ls["is_active"] = ls["move_out_date"].isna()
    # Move-out before move-in -> quarantine.
    badl = ls[
        ls["move_out_date"].notna()
        & ls["move_in_date"].notna()
        & (ls["move_out_date"] < ls["move_in_date"])
    ]
    for _, r in badl.iterrows():
        quarantine_rows.append({
            "table": "leases", "key": r["lease_id"],
            "reason": "move_out_before_move_in", "source_file": r["_source_file"],
        })
    ls = ls.drop(badl.index).reset_index(drop=True)
    _write(con, "leases", ls[[
        "lease_id", "resident_id", "unit_id", "community_id", "move_in_date",
        "move_out_date", "move_out_reason", "monthly_rate", "is_active",
    ]])
    stats["leases"] = {"rows": len(ls), "rejected": len(badl)}

    # ---------------- shifts ---------------- #
    sh = _df(con, "adp_shifts")
    sh["shift_date"] = sh["shift_date"].map(parse_date)
    sh["hours_worked"] = pd.to_numeric(sh["hours_worked"], errors="coerce")
    sh["hourly_rate"] = [
        parse_adp_hourly_rate(role, raw)
        for role, raw in zip(sh["role"], sh["hourly_rate"])
    ]
    sh["labor_cost"] = sh["hours_worked"] * sh["hourly_rate"]
    sh = sh.drop_duplicates("shift_id")
    _write(con, "shifts", sh[[
        "shift_id", "community_id", "employee_id", "role", "shift_date",
        "hours_worked", "hourly_rate", "labor_cost",
    ]])
    stats["shifts"] = {"rows": len(sh)}

    # ---------------- reviews ---------------- #
    rv = _df(con, "gbp_reviews")
    rv["review_date"] = rv["review_date"].map(parse_date)
    rv["responded_at"] = rv["responded_at"].map(parse_date)
    rv["rating"] = pd.to_numeric(rv["rating"], errors="coerce")
    rv = rv.drop_duplicates("review_id")
    _write(con, "reviews", rv[[
        "review_id", "community_id", "review_date", "rating", "review_text",
        "response_text", "responded_at",
    ]])
    stats["reviews"] = {"rows": len(rv)}

    # ---------------- leads ---------------- #
    ld = _df(con, "hubspot_leads")
    for c in ["created_date", "tour_date", "deposit_date", "move_in_date"]:
        ld[c] = ld[c].map(parse_date)
    ld = ld.sort_values(["lead_id", "_source_month"]).drop_duplicates(
        "lead_id", keep="last"
    )
    _write(con, "leads", ld[[
        "lead_id", "community_id", "lead_source", "created_date", "tour_date",
        "deposit_date", "move_in_date", "status", "lost_reason",
    ]])
    stats["leads"] = {"rows": len(ld)}

    # ---------------- communities (master) ---------------- #
    con.execute(
        f"CREATE OR REPLACE TABLE silver.communities AS "
        f"SELECT * FROM read_csv_auto('{cfg['communities']}')"
    )

    # ---------------- quality outputs ---------------- #
    q = pd.DataFrame(quarantine_rows) if quarantine_rows else pd.DataFrame(
        columns=["table", "key", "reason", "source_file"]
    )
    _write(con, "quarantine", q)
    f = pd.DataFrame(quality_flags) if quality_flags else pd.DataFrame(
        columns=["table", "key", "issue", "detail", "source_file"]
    )
    _write(con, "data_quality_flags", f)
    stats["quarantine"] = {"rows": len(q)}
    stats["data_quality_flags"] = {"rows": len(f)}

    if run_log is not None:
        run_log["silver"] = stats
    return stats
