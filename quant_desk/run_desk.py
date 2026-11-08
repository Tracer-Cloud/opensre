"""Run the whole desk: fetch -> ArcticDB -> indicators -> csp sweep -> charts -> report.

.venv/bin/python run_desk.py              # fetch fresh 30 days, then everything
.venv/bin/python run_desk.py --no-fetch   # reuse the bars already in ArcticDB
"""

from __future__ import annotations

import argparse
import logging
import warnings

import pandas as pd

from desk import chart, config, data, indicators, report, strategy, sweep

warnings.filterwarnings("ignore", category=SyntaxWarning)  # gs-quant docstring escapes


def _setup_logging() -> None:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    agent = logging.getLogger("desk.agent")
    handler = logging.FileHandler(config.OUTPUT_DIR / "agent.log", mode="w")
    handler.setFormatter(logging.Formatter("%(message)s"))
    agent.addHandler(handler)
    agent.propagate = False  # keep 100 trade lines out of the console; they go to agent.log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true", help="reuse bars stored in ArcticDB")
    args = parser.parse_args()
    _setup_logging()
    log = logging.getLogger("desk")

    if not args.no_fetch:
        data.ingest()
    bars = data.load(config.SYMBOL_BARS)
    log.info("read %d bars back from ArcticDB (%s -> %s)", len(bars), bars.index[0], bars.index[-1])

    combos = sweep.grid()
    runs = strategy.run(bars, combos)
    table = sweep.label_plateaus(sweep.summarise(runs, bars.index))
    data.store(table, config.SYMBOL_SWEEP, combos=len(combos), fee_bps=config.FEE_BPS)

    best = (int(table.iloc[0]["fast"]), int(table.iloc[0]["slow"]))
    # Replay the winner once more with the agent log attached, so the log and the chart
    # markers come from the same csp fills stream.
    shown = strategy.run(bars, [best], log_combos=[best])[best]
    pd.testing.assert_frame_equal(shown.fills, runs[best].fills)

    ind = indicators.indicator_frame(bars, *best)
    profile = indicators.volume_profile(bars)
    wf = sweep.walk_forward(runs, bars.index)

    out = config.OUTPUT_DIR
    charts = [
        chart.trades_chart(bars, ind, profile, shown, out / "trades.png"),
        chart.zoom_chart(bars, ind, shown, out / "trades_zoom.png"),
        chart.heatmap_chart(table, out / "sweep_heatmap.png"),
        chart.windows_chart(bars, table, runs, out / "windows.png"),
        chart.walk_forward_chart(bars, wf, shown, out / "walk_forward.png"),
    ]
    table.to_csv(out / "sweep.csv", index=False)
    report_path = report.write(bars, table, shown, ind, profile, wf, out / "REPORT.md")
    log.info("wrote %s and %d charts to %s", report_path.name, len(charts), out)


if __name__ == "__main__":
    main()
