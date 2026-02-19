"""Stage dispatcher -- routes to the correct stage module based on PIPELINE_STAGE env var."""

import sys

from config import PIPELINE_RUN_ID


def main() -> None:
    import os

    stage = os.getenv("PIPELINE_STAGE", "")
    if not stage:
        print("PIPELINE_ERROR: PIPELINE_STAGE env var not set", file=sys.stderr)
        sys.exit(1)

    print(f"[pipeline] stage={stage} run_id={PIPELINE_RUN_ID}")

    try:
        if stage == "extract":
            from .extract import main as run
        elif stage == "transform":
            from .transform import main as run
        elif stage == "load":
            from .load import main as run
        else:
            print(f"PIPELINE_ERROR: Unknown stage '{stage}'", file=sys.stderr)
            sys.exit(1)

        run()

    except Exception as e:
        print(f"PIPELINE_ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
