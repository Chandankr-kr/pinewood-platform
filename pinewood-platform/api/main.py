"""Pinewood Gold-layer API (FastAPI).

Run:  uvicorn api.main:app --reload
Docs: http://127.0.0.1:8000/docs   (OpenAPI / Swagger, free with FastAPI)

Every data endpoint requires a valid bearer token and filters results to the
caller's authorized communities, enforced server-side (see api/auth.py).
"""
from __future__ import annotations

from pathlib import Path

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Query

from pipeline.common import load_config
from .auth import authorize_communities, get_current_principal

app = FastAPI(
    title="Pinewood Senior Living — Operations API",
    description="Read-only access to the Gold layer with role-based row-level "
                "security. All endpoints require a bearer token.",
    version="1.0.0",
)

_CFG = load_config()


def get_con() -> duckdb.DuckDBPyConnection:
    """Open a fresh read-only connection per request."""
    con = duckdb.connect(_CFG["warehouse"], read_only=True)
    try:
        yield con
    finally:
        con.close()


def _placeholders(items: list[str]) -> str:
    return ", ".join("?" for _ in items)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.get("/me", tags=["meta"])
def me(principal: dict = Depends(get_current_principal),
       con=Depends(get_con)):
    """Echo the caller's identity and the communities they may see."""
    comms = authorize_communities(con, principal)
    return {
        "sub": principal["sub"], "role": principal["role"],
        "region": principal.get("region"),
        "community_id": principal.get("community_id"),
        "authorized_communities": comms,
    }


@app.get("/occupancy", tags=["operations"])
def occupancy(
    community_id: str | None = Query(None),
    start: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    end: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    principal: dict = Depends(get_current_principal),
    con=Depends(get_con),
):
    comms = authorize_communities(con, principal, requested_community_id=community_id)
    if not comms:
        return {"results": []}
    sql = (
        "SELECT community_id, community_name, region, month_start, total_units, "
        "occupied_units, occupancy_rate FROM gold.vw_monthly_occupancy "
        f"WHERE community_id IN ({_placeholders(comms)})"
    )
    params = list(comms)
    if start:
        sql += " AND month_start >= ?"; params.append(start)
    if end:
        sql += " AND month_start <= ?"; params.append(end)
    sql += " ORDER BY community_id, month_start"
    return {"results": con.execute(sql, params).df().to_dict("records")}


@app.get("/move-outs/reasons", tags=["operations"])
def move_out_reasons(
    community_id: str | None = Query(None),
    period: str | None = Query(None, description="YYYY-MM to filter move-out month"),
    principal: dict = Depends(get_current_principal),
    con=Depends(get_con),
):
    comms = authorize_communities(con, principal, requested_community_id=community_id)
    if not comms:
        return {"results": []}
    sql = (
        "SELECT community_id, COALESCE(move_out_reason,'Unknown') AS reason, "
        "COUNT(*) AS move_outs FROM gold.fact_moveouts "
        f"WHERE community_id IN ({_placeholders(comms)})"
    )
    params = list(comms)
    if period:
        sql += " AND strftime(move_out_month, '%Y-%m') = ?"; params.append(period)
    sql += " GROUP BY community_id, reason ORDER BY community_id, move_outs DESC"
    rows = con.execute(sql, params).df()
    # add pct within community
    if not rows.empty:
        rows["pct_of_total"] = (
            rows.groupby("community_id")["move_outs"].transform(
                lambda s: (100.0 * s / s.sum()).round(1)
            )
        )
    return {"results": rows.to_dict("records")}


@app.get("/incidents/summary", tags=["operations"])
def incidents_summary(
    region: str | None = Query(None),
    community_id: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    principal: dict = Depends(get_current_principal),
    con=Depends(get_con),
):
    comms = authorize_communities(
        con, principal, requested_community_id=community_id, requested_region=region
    )
    if not comms:
        return {"results": []}
    sql = (
        "SELECT community_id, care_level, incident_type, COUNT(*) AS incidents, "
        "AVG(severity) AS avg_severity FROM gold.fact_incidents "
        f"WHERE community_id IN ({_placeholders(comms)})"
    )
    params = list(comms)
    if start:
        sql += " AND incident_date >= ?"; params.append(start)
    if end:
        sql += " AND incident_date <= ?"; params.append(end)
    sql += " GROUP BY community_id, care_level, incident_type ORDER BY community_id, incidents DESC"
    return {"results": con.execute(sql, params).df().to_dict("records")}


@app.get("/labor/cost", tags=["operations"])
def labor_cost(
    community_id: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    principal: dict = Depends(get_current_principal),
    con=Depends(get_con),
):
    comms = authorize_communities(con, principal, requested_community_id=community_id)
    if not comms:
        return {"results": []}
    sql = (
        "SELECT community_id, month_start, labor_cost, resident_days, "
        "labor_cost_per_resident_day FROM gold.vw_labor_cost_per_resident_day "
        f"WHERE community_id IN ({_placeholders(comms)})"
    )
    params = list(comms)
    if start:
        sql += " AND month_start >= ?"; params.append(start)
    if end:
        sql += " AND month_start <= ?"; params.append(end)
    sql += " ORDER BY community_id, month_start"
    return {"results": con.execute(sql, params).df().to_dict("records")}


@app.get("/reviews/summary", tags=["operations"])
def reviews_summary(
    community_id: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    principal: dict = Depends(get_current_principal),
    con=Depends(get_con),
):
    comms = authorize_communities(con, principal, requested_community_id=community_id)
    if not comms:
        return {"results": []}
    sql = (
        "SELECT community_id, month_start, COUNT(*) AS reviews, "
        "ROUND(AVG(rating),2) AS avg_rating, "
        "ROUND(100.0*AVG(CASE WHEN has_response THEN 1 ELSE 0 END),1) AS response_rate_pct "
        "FROM gold.fact_reviews "
        f"WHERE community_id IN ({_placeholders(comms)})"
    )
    params = list(comms)
    if start:
        sql += " AND review_date >= ?"; params.append(start)
    if end:
        sql += " AND review_date <= ?"; params.append(end)
    sql += " GROUP BY community_id, month_start ORDER BY community_id, month_start"
    return {"results": con.execute(sql, params).df().to_dict("records")}
