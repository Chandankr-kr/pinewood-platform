# Pinewood Senior Living — Data & Analytics Platform

A minimum-viable analytics platform for Pinewood Senior Living: it ingests six
months of messy CSV exports from five source systems, lands them through a
Bronze → Silver → Gold medallion architecture in **DuckDB**, validates every
run, exposes the Gold layer through a **FastAPI** service with role-based
row-level security, and feeds a **Power BI** Executive Operations Dashboard.

- **Storage:** DuckDB (single local file, no cloud account).
- **Language:** Python 3.10+ (built and tested on 3.13).
- **API:** FastAPI + JWT bearer auth with server-side RLS.
- **BI:** Power BI Desktop (model, DAX, and RLS provided; see `powerbi/`).

> **Walkthrough video:** _[paste your Loom/OBS link here]_

---

## Repository layout

```
pipeline/        Python ingestion (Bronze/Silver/Gold) + run log + gold export
sql/             Gold DDL (PK/FK/grain) and the analytics views
api/             FastAPI service, JWT auth/RLS, test-token generator
powerbi/         DAX measures, model + relationship spec, RLS roles, Gold CSVs
validation/      Validation framework + latest report + per-run reports
communication/   Email to IT, and the CFO incident-response reply
config/          settings.yaml and the derived community master
requirements.txt
```

---

## Quick start (fresh machine)

```powershell
# 1. From the repo root, create a venv and install deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Run the whole pipeline (Bronze -> Silver -> Gold -> validation). One command.
python -m pipeline.run_pipeline          # add --force to fully rebuild

# 3. Export the Gold layer to CSV for Power BI
python -m pipeline.export_gold

# 4. Generate the three test tokens (one per role)
python -m api.generate_tokens

# 5. Start the API (Swagger at http://127.0.0.1:8000/docs)
uvicorn api.main:app --port 8000
```

By default the pipeline reads the dataset from
`../Pinewood_Dataset (1)/candidate_package/data`. Override with:

```powershell
$env:PINEWOOD_DATA_DIR = "C:\path\to\data"
```

---

## Architecture & design decisions

### Medallion layers
- **Bronze** (`pipeline/ingest.py`) — raw rows exactly as exported, plus
  ingestion metadata (`_source_system`, `_source_file`, `_source_month`,
  `_ingested_at`, `_row_hash`). Nothing is cleaned here; Bronze is the audit
  trail.
- **Silver** (`pipeline/transform.py`) — typed, deduplicated, conformed. Care
  levels normalized to one canonical set, all dates parsed to real `DATE`s,
  ADP pay rates recovered, suspect rows quarantined, soft issues flagged.
- **Gold** (`pipeline/gold.py`) — star schema the API and report consume.

### Rerunnability, incremental load, schema drift
- **Idempotent:** every file is tracked in `meta.ingested_files`. A re-run skips
  files already loaded; `--force` deletes a file's prior Bronze rows before
  reloading, so data never duplicates.
- **Incremental:** dropping a new `*_2025_07.csv` into the data folder lands only
  that file on the next run — no reprocessing of earlier months.
- **Schema drift:** the April residents export adds a `mobility_status` column.
  Bronze evolves the table (adds the column as NULL for older rows) instead of
  crashing. Newer columns require no code change.

### Run log
Every run writes `validation/run_reports/run_<ts>.json` and `.md` with rows in /
out / rejected per table, duration, and the validation summary.

---

## Data model (Gold star schema)

Full DDL with primary keys, foreign keys and comments: [`sql/gold_ddl.sql`](sql/gold_ddl.sql).

### Fact grains
| Fact table | Grain |
|---|---|
| `fact_occupancy_monthly` | one row per community per month |
| `fact_census_monthly` | one row per community per care level per month |
| `fact_labor_monthly` | one row per community per role per month |
| `fact_revenue_monthly` | one row per community per month |
| `fact_incidents` | one row per incident |
| `fact_moveouts` | one row per completed lease (move-out) |
| `fact_reviews` | one row per Google review |
| `fact_leads` | one row per HubSpot lead |
| `fact_resident_acuity_monthly` | one row per resident per month |

### Conformed dimensions
`dim_community`, `dim_date`, and `dim_care_level` are conformed across the
relevant facts; `dim_resident` is shared by every resident-grain fact and by the
SCD2 dimension.

### SCD Type 2 — resident care level
`dim_resident_care_scd2` tracks care-level history. A resident who moves Assisted
Living → Memory Care produces **two rows** with non-overlapping
`effective_date`/`end_date` windows (the current row has `end_date IS NULL` and
`is_current = TRUE`) — built from admit date + `pcc_care_history` events, never
an overwrite.

### Required SQL views (`sql/gold_views.sql`)
1. `vw_monthly_occupancy` — monthly occupancy rate by community
2. `vw_avg_los_by_care_level` — avg length of stay by care level, discharges in
   the last 12 months
3. `vw_top3_moveout_reasons` — top three move-out reasons per community (trailing
   12 months) as a % of total
4. `vw_labor_cost_per_resident_day` — labor cost per resident-day by community by
   month
5. `vw_incident_rate` — incidents per 100 resident-days by community and care
   level
6. `vw_acuity_escalation_candidates` — residents whose acuity rose ≥2 within a
   90-day window (care-review candidate list)

---

## API

JWT bearer tokens (HS256). The role + scope live inside the signed token, so
authorization is enforced **server-side** from the claims — the client can never
widen its own scope (`api/auth.py`).

| Endpoint | Description |
|---|---|
| `GET /occupancy?community_id=&start=&end=` | Monthly occupancy |
| `GET /move-outs/reasons?community_id=&period=` | Move-out reasons + % |
| `GET /incidents/summary?region=&community_id=&start=&end=` | Incident summary |
| `GET /labor/cost?community_id=&start=&end=` | Labor cost per resident-day |
| `GET /reviews/summary?community_id=&start=&end=` | Review ratings + response rate |
| `GET /me` | Caller identity + authorized communities |
| `GET /docs` | OpenAPI / Swagger UI |

**Roles & RLS:** `corporate_admin` sees all 14 communities; `regional_director`
sees only their region (e.g. Pacific Northwest = C001–C005); `community_ed` sees
only their community (e.g. C011). Requesting a community outside scope returns
**403**; unauthenticated requests return **401**. `python -m api.generate_tokens`
writes the three test tokens to `api/test_tokens.json`.

---

## Validation framework

`validation/validate.py` runs after every pipeline execution and writes
`validation/latest_validation_report.md`. It covers:

- **Row-count reconciliation** source → Bronze → Silver (and Silver ≤ Bronze).
- **Aggregate reconciliation** within documented tolerances (default 0.5%):
  shift hours Silver↔Gold, positive labor cost, positive resident-days.
- **Business rules:** no discharge-before-admit, no move-out-before-move-in, no
  future-dated events, acuity in range, no negative occupancy/hours, occupancy
  ≤ 100%, no overlapping leases per resident, lease→resident FK integrity, ADP
  rate fully recovered, care levels canonical.
- **Anomaly summary** with severity and a recommended action
  (`fix_in_pipeline` / `raise_to_client` / `quarantine` / `monitor`).

Latest run: **18/18 checks pass**, with the source-data anomalies below surfaced
for the client.

---

## Anomalies found in the data

| # | Anomaly | How it was found | How it's handled |
|---|---|---|---|
| 1 | **`care_level` has 9 spellings** (`AL`, `Assisted`, `Assisted Living`, `MC`, `Memory`, …) | Distinct-value profiling | Normalized to a canonical 3-value set in Silver (`normalize_care_level`) |
| 2 | **ADP `hourly_rate` is a role-keyed dict**, not a number (`"{'Caregiver':16,'RN':46,…}"`) | Column profiling on 68k shift rows | Recovered the real rate by looking up each shift's own `role` in the dict; `labor_cost = hours × recovered_rate` |
| 3 | **Mixed date formats** in PCC residents (`YYYY-MM-DD` and `M/D/YYYY`, 683 US-format rows) | Date-pattern scan | `parse_date` accepts both and emits a single `DATE` type |
| 4 | **Acuity out of range** (values like `-5` and `99`; 18 rows) | Min/max scan (valid is 1–10) | Nulled the invalid score (kept the row), flagged it, raised to client |
| 5 | **Schema drift** — `mobility_status` appears only from the April residents file | Header diff across months | Bronze auto-adds the column (NULL for older rows); pipeline does not crash |
| 6 | **Duplicate leases across files** — 44 `lease_id`s reappear in their move-out month | Cross-file key-count | Deduped to the latest record per `lease_id` in Silver |
| 7 | **Duplicate lead** — 1 `lead_id` appears in two monthly files | Cross-file key-count | Deduped to the latest record per `lead_id` |
| 8 | **Gender / first-name mismatches** (e.g. "Ruth … M", "Michael … F") | Name-vs-gender heuristic | **Flagged, not silently changed** — it's a source-of-truth question; surfaced to the client |
| 9 | **Yardi leases are transactional, not a snapshot** — only 302 leases vs 823 active PCC residents, so counting "active leases" badly undercounts occupancy | Reconciling resident counts across PCC and Yardi | Occupancy is computed from the **PCC active-resident census** (a true monthly snapshot) over licensed units, not from the partial lease feed |
| 10 | **Acuity score is static** — no resident's `acuity_score` changes across the six monthly snapshots, despite "Acuity Increase" rows in `pcc_care_history` | Per-resident acuity variance check | The 90-day acuity-escalation view is correct but legitimately empty; flagged the static feed as a data-quality issue to raise with the client |
| 11 | **Missing move-out reasons** — 227 leases moved out with a blank reason | Distinct-value profiling | Bucketed as `Unknown` in the move-out views so percentages still reconcile |

> Per the brief, the emphasis is on finding a handful and explaining how each is
> handled, not on listing the most. Each entry above corresponds to code in
> `pipeline/transform.py` and/or a check in `validation/validate.py`, and the
> validation framework would re-catch the same class of problem in a future
> month (e.g. a new care-level spelling fails `care_levels_canonical`, a future
> date fails `no_future_dates_*`).

---

## Power BI

See [`powerbi/README_POWERBI.md`](powerbi/README_POWERBI.md) for the model,
relationships (all single-direction; `dim_date` is a role-playing dimension on
move-in/move-out), and build steps; [`powerbi/measures.dax`](powerbi/measures.dax)
for every required measure (current occupancy %, YoY revenue %, average daily
census, trailing-90-day move-out rate, incident rate per 100 resident-days, a
rolling-90-day time-intelligence measure, and an `AVERAGEX`+`CALCULATE` measure
that relies on **context transition**); and [`powerbi/RLS.md`](powerbi/RLS.md)
for the two RLS roles and the View-As demo.

---

## Communication artifacts

- [`communication/email_to_IT.md`](communication/email_to_IT.md) — access request
  to Karen Mills.
- [`communication/email_incident_response.md`](communication/email_incident_response.md)
  — CFO occupancy-discrepancy reply.
