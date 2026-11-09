"""Multi-year study + every robustness check + multi-coin test + one paper-trading step.

.venv/bin/python run_history.py              # fetch fresh data, then everything
.venv/bin/python run_history.py --no-fetch   # reuse the data stored in ArcticDB
"""

from __future__ import annotations

import argparse
import logging
import warnings

import pandas as pd

from desk import (
    config,
    data,
    history,
    history_chart,
    history_report,
    multi_asset,
    paper,
    robustness,
    robustness_report,
    sweep,
)

warnings.filterwarnings("ignore", category=SyntaxWarning)  # gs-quant docstring escapes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true", help="reuse data in ArcticDB")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("desk.data").setLevel(logging.WARNING)
    log = logging.getLogger("desk")

    if args.no_fetch:
        lib = data.library()
        symbols = [s for s in config.ASSETS if lib.has_symbol(multi_asset.arctic_symbol(s))]
    else:
        data.ingest_history()
        symbols = multi_asset.ingest_assets()

    out = config.OUTPUT_DIR / "history"
    sweeps = out / "sweeps"
    sweeps.mkdir(parents=True, exist_ok=True)

    log.info("study: %d variants x %d bar sizes", 10, len(config.TIMEFRAMES))
    study = history.run_study()
    summary = history.summary(study)
    summary.to_csv(out / "summary.csv", index=False)
    for cell in study.cells:
        cell.table.to_csv(sweeps / f"{cell.timeframe}_{cell.variant.name}.csv", index=False)
    cand = {
        cost: history.candidates(study, cost, config.EXECUTION)
        for cost in (config.COST_BPS, config.OPTIMISTIC_COST_BPS)
    }
    pd.concat(cand.values()).to_csv(out / "candidates.csv", index=False)
    history_chart.wf_sharpe_chart(summary, out / "wf_sharpe.png")
    history_chart.heatmaps_chart(study, "trend", out / "heatmaps_trend.png")
    history_chart.heatmaps_chart(study, "mean_reversion", out / "heatmaps_mean_reversion.png")
    history_chart.wf_equity_chart(study, out / "wf_equity.png")
    history_chart.candidates_chart(study, out / "candidates.png")
    history_report.write(study, summary, cand, out / "HISTORY_REPORT.md")

    log.info("robustness: execution, meta walk-forward, deflated Sharpe, sensitivity")
    ex = robustness.execution_test(study)
    ex.to_csv(out / "execution_test.csv", index=False)
    daily = robustness.daily_books(study)
    meta = robustness.meta_walk_forward(daily)
    meta.steps.to_csv(out / "meta_walk_forward.csv", index=False)
    span = daily.index[sweep.window_slices(len(daily), config.HISTORY_WINDOWS)[1].start :]
    n_sets = int(summary["combos"].sum())
    dsr = robustness.deflated_sharpe_table(daily, span, n_sets)
    dsr.to_csv(out / "deflated_sharpe.csv", index=False)
    sens = robustness.sensitivity()
    sens.to_csv(out / "sensitivity.csv", index=False)

    log.info("other coins: %s", ", ".join(symbols))
    assets = multi_asset.run_assets(symbols)
    per = multi_asset.per_asset(assets)
    per.to_csv(out / "assets.csv", index=False)
    port, curves = multi_asset.portfolio(assets)
    port.to_csv(out / "portfolio.csv", index=False)
    history_chart.portfolio_chart(curves, out / "portfolio.png")

    log.info("paper trading: one step for %s", [b.key for b in paper.paper_books()])
    status = paper.run_once()

    report = robustness_report.write(
        study,
        ex,
        meta,
        daily,
        dsr,
        n_sets,
        robustness.sensitivity_summary(sens),
        multi_asset.pass_counts(per),
        port,
        per,
        status,
        out / "ROBUSTNESS_REPORT.md",
    )
    log.info("wrote %s", report)


if __name__ == "__main__":
    main()
