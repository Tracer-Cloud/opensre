"""Multi-year study: every variant x 15m/1h/4h -> plateaus, fee stress, walk-forward, report.

.venv/bin/python run_history.py              # fetch history since 2022, then run the study
.venv/bin/python run_history.py --no-fetch   # reuse the history stored in ArcticDB
"""

from __future__ import annotations

import argparse
import logging
import warnings

from desk import config, data, history, history_chart, history_report

warnings.filterwarnings("ignore", category=SyntaxWarning)  # gs-quant docstring escapes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true", help="reuse history in ArcticDB")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("desk.data").setLevel(logging.WARNING)

    if not args.no_fetch:
        data.ingest_history()
    study = history.run_study()
    summary = history.summary(study)

    out = config.OUTPUT_DIR / "history"
    sweeps = out / "sweeps"
    sweeps.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out / "summary.csv", index=False)
    for cell in study.cells:
        cell.table.to_csv(sweeps / f"{cell.timeframe}_{cell.variant.name}.csv", index=False)
    history_chart.wf_sharpe_chart(summary, out / "wf_sharpe.png")
    history_chart.heatmaps_chart(study, "trend", out / "heatmaps_trend.png")
    history_chart.heatmaps_chart(study, "mean_reversion", out / "heatmaps_mean_reversion.png")
    history_chart.wf_equity_chart(study, out / "wf_equity.png")
    report = history_report.write(study, summary, out / "HISTORY_REPORT.md")
    logging.getLogger("desk").info("wrote %s", report)


if __name__ == "__main__":
    main()
