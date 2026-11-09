"""Paper-trade the pre-registered books (config.PAPER_BOOKS).

.venv/bin/python run_paper.py          # one step: record decisions for newly closed bars
.venv/bin/python run_paper.py --loop   # run 24/7, one step ~90 s after every bar closes

Cron alternative (every 4h, 2 minutes past):  2 */4 * * *  cd /path/to/quant_desk && \
    .venv/bin/python run_paper.py >> output/paper/paper.log 2>&1
"""

from __future__ import annotations

import argparse
import logging
import warnings

from desk import paper

warnings.filterwarnings("ignore", category=SyntaxWarning)  # gs-quant docstring escapes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true", help="keep running, one step per bar")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("desk.data").setLevel(logging.WARNING)
    if args.loop:
        paper.run_loop()
    else:
        print(paper.run_once())


if __name__ == "__main__":
    main()
