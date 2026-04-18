import sys

from config import PIPELINE_STAGE


def main() -> None:
    stage = PIPELINE_STAGE.lower()
    if stage == "ingest":
        from stages.ingest import main as run
    elif stage == "validate":
        from stages.validate import main as run
    elif stage == "publish":
        from stages.publish import main as run
    elif stage == "crashloop":
        from stages.crashloop import main as run
    else:
        print(f"PIPELINE_ERROR: unknown stage '{stage}'", file=sys.stderr)
        sys.exit(1)

    run()
