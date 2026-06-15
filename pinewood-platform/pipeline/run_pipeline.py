"""Single-command pipeline orchestrator.

    python -m pipeline.run_pipeline            # incremental run
    python -m pipeline.run_pipeline --force    # reload every source file

Runs Bronze -> Silver -> Gold, then the validation framework, and writes a run
log (JSON + Markdown) summarizing rows in/out/rejected, timings, and anomalies.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT, connect, load_config
from .gold import build_gold
from .ingest import ingest
from .transform import transform


def _write_run_log(run_log: dict):
    out_dir = ROOT / "validation" / "run_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    (out_dir / f"run_{ts}.json").write_text(
        json.dumps(run_log, indent=2, default=str), encoding="utf-8"
    )

    lines = [f"# Pipeline Run Log — {run_log['started_at']}", ""]
    lines.append(f"- Mode: {'force reload' if run_log['force'] else 'incremental'}")
    lines.append(f"- Total duration: {run_log['duration_seconds']}s")
    lines.append("")
    for layer in ("bronze", "silver", "gold"):
        if layer not in run_log:
            continue
        lines.append(f"## {layer.title()}")
        lines.append("")
        lines.append("| Table | Rows |")
        lines.append("|---|---|")
        for tbl, s in run_log[layer].items():
            rows = s.get("total_rows", s.get("rows", s.get("rows_loaded_this_run", "")))
            lines.append(f"| {tbl} | {rows} |")
        lines.append("")
    if "validation" in run_log:
        v = run_log["validation"]
        lines.append("## Validation")
        lines.append("")
        lines.append(f"- Status: **{v.get('status')}**")
        lines.append(f"- Checks passed: {v.get('passed')} / {v.get('total_checks')}")
        lines.append(f"- Anomalies detected: {v.get('anomaly_count')}")
        lines.append("")
        lines.append(f"Full report: `validation/run_reports/validation_{ts}.md`")
    (out_dir / f"run_{ts}.md").write_text("\n".join(lines), encoding="utf-8")
    return ts


def main():
    parser = argparse.ArgumentParser(description="Run the Pinewood data pipeline.")
    parser.add_argument("--force", action="store_true",
                        help="Reload every source file (idempotent rebuild).")
    parser.add_argument("--skip-validation", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    run_log: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "force": args.force,
    }
    t0 = time.time()

    con = connect(cfg)
    print("Bronze: ingesting raw CSVs...")
    ingest(con, cfg, force=args.force, run_log=run_log)
    print("Silver: cleaning & conforming...")
    transform(con, cfg, run_log=run_log)
    print("Gold: building star schema...")
    build_gold(con, cfg, run_log=run_log)

    run_log["duration_seconds"] = round(time.time() - t0, 2)

    if not args.skip_validation:
        print("Validation: reconciling layers & checking business rules...")
        from validation.validate import run_validation
        v_summary = run_validation(con, cfg)
        run_log["validation"] = v_summary

    ts = _write_run_log(run_log)
    con.close()
    print(f"\nDone in {run_log['duration_seconds']}s. Run log: "
          f"validation/run_reports/run_{ts}.md")


if __name__ == "__main__":
    main()
