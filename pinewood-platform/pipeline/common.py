"""Shared helpers: config loading, DuckDB connection, and value-cleaning
functions used across the Bronze/Silver/Gold layers.

Everything here is deliberately small and explicit. The cleaning rules encode
decisions we made about the messy source data; each one is commented so the
choice can be defended.
"""
from __future__ import annotations

import ast
import os
import re
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import yaml

# Repo root = parent of the `pipeline/` package.
ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    """Load config/settings.yaml and resolve paths relative to the repo root."""
    with open(ROOT / "config" / "settings.yaml", "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["data_dir"] = os.environ.get(
        "PINEWOOD_DATA_DIR", str((ROOT / cfg["data_dir"]).resolve())
    )
    cfg["warehouse"] = str((ROOT / cfg["warehouse"]).resolve())
    cfg["communities"] = str((ROOT / cfg["communities"]).resolve())
    return cfg


def connect(cfg: dict) -> duckdb.DuckDBPyConnection:
    """Open (and create if needed) the DuckDB warehouse with schemas."""
    Path(cfg["warehouse"]).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(cfg["warehouse"])
    for schema in ("bronze", "silver", "gold", "meta"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    return con


# --------------------------------------------------------------------------- #
# Value cleaning
# --------------------------------------------------------------------------- #

# Canonical care levels. Every source variant maps to exactly one of these.
CARE_LEVEL_CANONICAL = {
    "IL": "Independent Living",
    "INDEPENDENT": "Independent Living",
    "INDEPENDENT LIVING": "Independent Living",
    "AL": "Assisted Living",
    "ASSISTED": "Assisted Living",
    "ASSISTED LIVING": "Assisted Living",
    "MC": "Memory Care",
    "MEMORY": "Memory Care",
    "MEMORY CARE": "Memory Care",
}

# Short code used for unit_type and compact keys.
CARE_LEVEL_CODE = {
    "Independent Living": "IL",
    "Assisted Living": "AL",
    "Memory Care": "MC",
}


def normalize_care_level(value) -> str | None:
    """Map any of the 9 observed care-level spellings to the canonical set."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    key = str(value).strip().upper()
    if not key:
        return None
    return CARE_LEVEL_CANONICAL.get(key)


def parse_date(value) -> date | None:
    """Parse ISO (YYYY-MM-DD) or US (M/D/YYYY) dates into a date object.

    The PCC residents export mixes both formats; this normalizes them.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_adp_hourly_rate(role: str, raw_value) -> float | None:
    """ADP exported `hourly_rate` as a Python-dict string keyed by role
    (e.g. "{'Caregiver': 16, 'RN': 46, ...}") instead of a scalar rate.

    We recover the real rate by looking up the shift's own role inside that
    dict. If the value is already numeric we use it directly.
    """
    if raw_value is None:
        return None
    s = str(raw_value).strip()
    if not s:
        return None
    if s.startswith("{"):
        try:
            rates = ast.literal_eval(s)
            if isinstance(rates, dict) and role in rates:
                return float(rates[role])
        except (ValueError, SyntaxError):
            return None
        return None
    try:
        return float(s)
    except ValueError:
        return None


def clean_acuity(value) -> tuple[int | None, bool]:
    """Return (cleaned_score, is_valid). Valid acuity is an integer 1..10.

    Out-of-range values (we saw -5 and 99) are flagged invalid and nulled so
    they cannot poison averages; the row is still kept and reported.
    """
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return None, False
    if 1 <= v <= 10:
        return v, True
    return None, False


def month_from_filename(path: str) -> str:
    """Extract the YYYY-MM token from a `{source}_{table}_{YYYY_MM}.csv` name."""
    m = re.search(r"(\d{4})_(\d{2})\.csv$", os.path.basename(path))
    return f"{m.group(1)}-{m.group(2)}" if m else "unknown"
