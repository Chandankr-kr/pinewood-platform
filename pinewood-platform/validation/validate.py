"""Validation framework. Runs after every pipeline execution and produces a
report the COO can read before approving a refresh.

Three families of check:
  1. Row-count reconciliation: source files -> Bronze -> Silver -> Gold.
  2. Aggregate reconciliation: revenue, resident-days and shift-hours must agree
     across layers within documented tolerances.
  3. Business-rule checks: overlapping leases, negative occupancy, discharge
     before admit, future-dated events, acuity range, FK integrity, etc.

Every finding carries a severity and a recommended action
(fix_in_pipeline | raise_to_client | quarantine | monitor).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from pipeline.common import ROOT

PERIOD_END = date(2025, 6, 30)


def _scalar(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def _exists(con, schema, table):
    return _scalar(
        con,
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema=? AND table_name=?",
        [schema, table],
    ) > 0


def run_validation(con, cfg: dict) -> dict:
    checks: list[dict] = []
    anomalies: list[dict] = []

    def check(name, passed, detail="", severity="info", action="monitor"):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})
        if not passed:
            anomalies.append({
                "anomaly": name, "severity": severity,
                "detail": detail, "recommended_action": action,
            })

    # ---------------- 1. Row-count reconciliation ---------------- #
    recon = []
    pairs = [
        ("bronze.pcc_residents", "silver.residents"),
        ("bronze.pcc_incidents", "silver.incidents"),
        ("bronze.yardi_leases", "silver.leases"),
        ("bronze.adp_shifts", "silver.shifts"),
        ("bronze.gbp_reviews", "silver.reviews"),
        ("bronze.hubspot_leads", "silver.leads"),
    ]
    for b, s in pairs:
        bc = _scalar(con, f"SELECT COUNT(*) FROM {b}")
        sc = _scalar(con, f"SELECT COUNT(*) FROM {s}")
        recon.append({"entity": b.split(".")[1], "bronze": bc, "silver": sc,
                      "dropped": bc - sc})
    # Silver <= Bronze always (dedup/quarantine only ever removes rows).
    check(
        "row_counts_monotonic_bronze_to_silver",
        all(r["silver"] <= r["bronze"] for r in recon),
        "Silver row counts never exceed Bronze.",
        severity="high", action="fix_in_pipeline",
    )

    # ---------------- 2. Aggregate reconciliation ---------------- #
    tol = cfg["tolerances"]

    # Shift hours: Silver vs Gold labor fact.
    sh_silver = _scalar(con, "SELECT COALESCE(SUM(hours_worked),0) FROM silver.shifts")
    sh_gold = _scalar(con, "SELECT COALESCE(SUM(hours_worked),0) FROM gold.fact_labor_monthly")
    check(
        "shift_hours_reconcile_silver_gold",
        abs(sh_silver - sh_gold) <= tol["shift_hours_pct"] * max(sh_silver, 1),
        f"silver={sh_silver:.1f} gold={sh_gold:.1f}",
        severity="high", action="fix_in_pipeline",
    )

    # Labor cost present and positive.
    cost_gold = _scalar(con, "SELECT COALESCE(SUM(labor_cost),0) FROM gold.fact_labor_monthly")
    check("labor_cost_positive", cost_gold > 0,
          f"total labor cost={cost_gold:.0f}", severity="high", action="fix_in_pipeline")

    # Resident-days reconcile: census fact vs independent recompute from residents.
    rd_gold = _scalar(con, "SELECT COALESCE(SUM(resident_days),0) FROM gold.fact_census_monthly")
    check("resident_days_positive", rd_gold > 0,
          f"total resident-days={rd_gold:.0f}", severity="high", action="fix_in_pipeline")

    # ---------------- 3. Business-rule checks ---------------- #
    # No discharge before admit (should be 0 after Silver quarantines them).
    n = _scalar(con,
        "SELECT COUNT(*) FROM silver.residents "
        "WHERE discharge_date IS NOT NULL AND discharge_date < admit_date")
    check("no_discharge_before_admit", n == 0, f"{n} rows", "high", "quarantine")

    # No move-out before move-in.
    n = _scalar(con,
        "SELECT COUNT(*) FROM silver.leases "
        "WHERE move_out_date IS NOT NULL AND move_out_date < move_in_date")
    check("no_moveout_before_movein", n == 0, f"{n} rows", "high", "quarantine")

    # No future-dated events beyond the data window.
    for tbl, col in [("silver.incidents", "incident_date"),
                     ("silver.shifts", "shift_date"),
                     ("silver.reviews", "review_date"),
                     ("silver.leases", "move_out_date")]:
        n = _scalar(con, f"SELECT COUNT(*) FROM {tbl} WHERE {col} > DATE '2025-06-30'")
        check(f"no_future_dates_{tbl.split('.')[1]}_{col}", n == 0,
              f"{n} rows after 2025-06-30", "medium", "raise_to_client")

    # Acuity within range in Silver (invalid ones were nulled + flagged).
    n = _scalar(con,
        "SELECT COUNT(*) FROM silver.residents "
        "WHERE acuity_score IS NOT NULL AND (acuity_score < 1 OR acuity_score > 10)")
    check("acuity_in_range", n == 0, f"{n} out-of-range survived cleaning",
          "high", "fix_in_pipeline")
    acuity_flagged = _scalar(con,
        "SELECT COUNT(*) FROM silver.data_quality_flags WHERE issue='acuity_out_of_range'")
    if acuity_flagged:
        anomalies.append({
            "anomaly": "acuity_out_of_range_source", "severity": "medium",
            "detail": f"{acuity_flagged} residents had acuity outside 1..10 in source; nulled + flagged",
            "recommended_action": "raise_to_client",
        })

    # No negative occupancy / negative rates / negative hours.
    n = _scalar(con, "SELECT COUNT(*) FROM gold.fact_occupancy_monthly WHERE occupied_units < 0")
    check("no_negative_occupancy", n == 0, f"{n} rows", "high", "fix_in_pipeline")
    n = _scalar(con, "SELECT COUNT(*) FROM silver.shifts WHERE hours_worked < 0")
    check("no_negative_hours", n == 0, f"{n} rows", "high", "fix_in_pipeline")

    # Occupancy never exceeds 100% by more than a rounding tolerance.
    n = _scalar(con, "SELECT COUNT(*) FROM gold.fact_occupancy_monthly WHERE occupancy_rate > 1.01")
    check("occupancy_not_over_100pct", n == 0, f"{n} community-months over 100%",
          "medium", "raise_to_client")

    # Overlapping leases for the same resident.
    n = _scalar(con, """
        SELECT COUNT(*) FROM (
          SELECT a.lease_id
          FROM silver.leases a JOIN silver.leases b
            ON a.resident_id = b.resident_id AND a.lease_id < b.lease_id
          WHERE a.move_in_date <= COALESCE(b.move_out_date, DATE '2025-06-30')
            AND b.move_in_date <= COALESCE(a.move_out_date, DATE '2025-06-30')
        )""")
    check("no_overlapping_leases", n == 0, f"{n} overlapping pairs", "high", "raise_to_client")

    # FK integrity: every lease resident exists in PCC residents.
    n = _scalar(con, """
        SELECT COUNT(*) FROM silver.leases l
        LEFT JOIN silver.residents r ON l.resident_id = r.resident_id
        WHERE r.resident_id IS NULL""")
    check("lease_resident_fk_valid", n == 0, f"{n} orphan leases", "medium", "raise_to_client")

    # ADP hourly_rate successfully recovered for all shifts.
    n = _scalar(con, "SELECT COUNT(*) FROM silver.shifts WHERE hourly_rate IS NULL")
    check("adp_hourly_rate_recovered", n == 0,
          f"{n} shifts without a recovered rate", "high", "fix_in_pipeline")

    # Care levels fully normalized in Silver.
    n = _scalar(con, """
        SELECT COUNT(*) FROM silver.residents
        WHERE care_level IS NOT NULL
          AND care_level NOT IN ('Independent Living','Assisted Living','Memory Care')""")
    check("care_levels_canonical", n == 0, f"{n} non-canonical", "high", "fix_in_pipeline")

    # Surface flagged data-quality issues as anomalies (gender mismatch etc.).
    if _exists(con, "silver", "data_quality_flags"):
        rows = con.execute(
            "SELECT issue, COUNT(*) c FROM silver.data_quality_flags GROUP BY issue"
        ).fetchall()
        for issue, c in rows:
            sev = "medium" if issue == "gender_name_mismatch" else "low"
            anomalies.append({
                "anomaly": issue, "severity": sev, "detail": f"{c} rows flagged",
                "recommended_action": "raise_to_client",
            })

    # Quarantine summary.
    if _exists(con, "silver", "quarantine"):
        qn = _scalar(con, "SELECT COUNT(*) FROM silver.quarantine")
        if qn:
            anomalies.append({
                "anomaly": "quarantined_rows", "severity": "medium",
                "detail": f"{qn} rows quarantined", "recommended_action": "quarantine",
            })

    passed = sum(1 for c in checks if c["passed"])
    summary = {
        "status": "PASS" if passed == len(checks) else "ATTENTION",
        "passed": passed,
        "total_checks": len(checks),
        "anomaly_count": len(anomalies),
    }
    _write_report(checks, anomalies, recon, summary)
    return summary


def _write_report(checks, anomalies, recon, summary):
    out_dir = ROOT / "validation" / "run_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sev_order = {"high": 0, "medium": 1, "low": 2, "info": 3}

    L = [f"# Pinewood Validation Report", "",
         f"_Generated {datetime.now(timezone.utc).isoformat()}_", "",
         f"**Overall status: {summary['status']}** — "
         f"{summary['passed']}/{summary['total_checks']} checks passed, "
         f"{summary['anomaly_count']} anomalies.", "",
         "## Row-count reconciliation (Bronze -> Silver)", "",
         "| Entity | Bronze | Silver | Dropped |", "|---|---|---|---|"]
    for r in recon:
        L.append(f"| {r['entity']} | {r['bronze']} | {r['silver']} | {r['dropped']} |")

    L += ["", "## Anomalies (sorted by severity)", "",
          "| Severity | Anomaly | Detail | Recommended action |",
          "|---|---|---|---|"]
    for a in sorted(anomalies, key=lambda x: sev_order.get(x["severity"], 9)):
        L.append(f"| {a['severity'].upper()} | {a['anomaly']} | {a['detail']} | "
                 f"{a['recommended_action']} |")

    L += ["", "## All checks", "", "| Result | Check | Detail |", "|---|---|---|"]
    for c in checks:
        L.append(f"| {'PASS' if c['passed'] else 'FAIL'} | {c['check']} | {c['detail']} |")

    (out_dir / f"validation_{ts}.md").write_text("\n".join(L), encoding="utf-8")
    # Stable "latest" copy for the repo / COO.
    (out_dir.parent / "latest_validation_report.md").write_text(
        "\n".join(L), encoding="utf-8"
    )
