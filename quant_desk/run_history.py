"""Multi-year study + robustness checks + other coins + survivorship-free universe + other
venues + long-only hold-out test + one paper-trading step.

.venv/bin/python run_history.py              # fetch fresh data, then everything
.venv/bin/python run_history.py --no-fetch   # reuse the data stored in ArcticDB
"""

from __future__ import annotations

import argparse
import logging
import warnings

import pandas as pd

from desk import (
    books,
    config,
    cross_venue,
    data,
    history,
    history_chart,
    history_report,
    long_short,
    multi_asset,
    open_issues_report,
    paper,
    robustness,
    robustness_report,
    sweep,
    universe,
    universe_portfolio,
    venues,
)

warnings.filterwarnings("ignore", category=SyntaxWarning)  # gs-quant docstring escapes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true", help="reuse data in ArcticDB")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("desk.data").setLevel(logging.WARNING)
    log = logging.getLogger("desk")

    lib = data.library()
    if args.no_fetch:
        symbols = [s for s in config.ASSETS if lib.has_symbol(multi_asset.arctic_symbol(s))]
        venue_ok = [v for v in ("bitstamp", "coinbase") if lib.has_symbol(venues.arctic_symbol(v))]
    else:
        data.ingest_history()
        symbols = multi_asset.ingest_assets()
        venue_ok = venues.ingest_venues()
        universe.ingest_daily()
        universe.build_membership()

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

    log.info("open issues: long-only hold-outs, survivorship-free universe, other venues")
    members = universe.load_membership()
    survivors = universe_portfolio.survivors_table()
    both = pd.concat([members, survivors])
    missing = both[~both["instrument"].map(lambda i: lib.has_symbol(universe.arctic_symbol(i)))]
    if args.no_fetch and not missing.empty:
        universe.ingest_members_4h(missing)
    elif not args.no_fetch:
        universe.ingest_members_4h(both)
    stored = [i for i in both["instrument"].unique() if lib.has_symbol(universe.arctic_symbol(i))]
    jobs = (
        [(multi_asset.arctic_symbol(s), tf) for s in symbols for tf in config.ASSET_TIMEFRAMES]
        + [(venues.arctic_symbol(v), tf) for v in venue_ok for tf in config.ASSET_TIMEFRAMES]
        + [(universe.arctic_symbol(i), tf) for i in stored for tf in config.UNIVERSE_TIMEFRAMES]
    )
    log.info("exposures: %d series x %d books", len(jobs), len(books.ALL_BOOKS))
    expos = books.run_exposures(jobs)

    ls_table = long_short.compare(expos, long_short.datasets(symbols))
    ls_table.to_csv(out / "long_short_holdout.csv", index=False)
    verdict = long_short.verdict(ls_table)
    univ, univ_curves = universe_portfolio.evaluate(
        expos, {"point-in-time": members, "survivors only": survivors}
    )
    univ.to_csv(out / "universe.csv", index=False)
    members.to_csv(out / "universe_membership.csv", index=False)
    dead = universe_portfolio.casualties(expos, members)
    dead.to_csv(out / "universe_casualties.csv", index=False)
    cmp = cross_venue.compare(expos)
    cmp.to_csv(out / "venues.csv", index=False)
    corr = cross_venue.correlations(expos)
    history_chart.holdouts_chart(univ_curves, expos, out / "holdouts.png")
    sections = "\n".join(
        [
            open_issues_report.long_short_section(ls_table, verdict, univ),
            open_issues_report.survivorship_section(univ, members, dead),
            open_issues_report.venue_section(cmp, corr),
            open_issues_report.still_open(ls_table, univ, cmp),
        ]
    )

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
        extra_sections=sections,
    )
    log.info("wrote %s", report)


if __name__ == "__main__":
    main()
