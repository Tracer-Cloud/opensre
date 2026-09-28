"""Launch an owned GitHub workload with renewable, installation-bound provenance."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config.constants.analytics import (
    ANALYTICS_EXECUTION_CONTEXT_ENV,
    ANALYTICS_EXECUTION_CONTEXT_PATH,
    ANALYTICS_RUNNER_AUDIENCE,
)
from config.constants.paths import OPENSRE_HOME_ENV, WIZARD_STORE_PATH_ENV


def fetch_github_token(identity: str) -> str:
    """Request a short-lived GitHub token bound to one runtime installation."""
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    separator = "&" if "?" in url else "?"
    request = Request(
        url + separator + urlencode({"audience": ANALYTICS_RUNNER_AUDIENCE + identity}),
        headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]},
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310 -- GitHub runner-provided HTTPS URL.
        token = json.load(response)["value"]
    if not isinstance(token, str) or not token or len(token) > 16_384:
        raise ValueError("Invalid runner identity response")
    return token


def write_context(directory: Path, identity: str, *, is_test: bool) -> None:
    """Atomically replace credentials so directory bind mounts observe renewal."""
    context = {
        "version": 1,
        "analytics_id": identity,
        "execution_origin": "github_actions",
        "is_test": is_test,
        "token": fetch_github_token(identity),
    }
    temporary = directory / "execution-context.tmp"
    temporary.write_text(json.dumps(context), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(directory / "execution-context.json")


def docker_command(image: str, directory: Path, command: list[str]) -> list[str]:
    """Mount execution context independently of ordinary CI environment flags."""
    user = ["--user", f"{os.getuid()}:{os.getgid()}"] if os.name == "posix" else []
    return [
        "docker",
        "run",
        "--rm",
        *user,
        "--mount",
        f"type=bind,source={directory},target=/run/opensre,readonly",
        "--mount",
        f"type=bind,source={directory / 'home'},target=/opensre-home",
        "--env",
        f"{OPENSRE_HOME_ENV}=/opensre-home",
        "--env",
        f"{WIZARD_STORE_PATH_ENV}=/opensre-home/opensre.json",
        image,
        *command,
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docker", help="Run inside this image with a mounted context and fresh profile"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--require-delivery", action="store_true", help="Require an accepted install event"
    )
    parser.add_argument(
        "--test", action="store_true", help="Mark emitted events as synthetic test traffic"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A workload command is required")
    # Missing controller authority must stop this runner-specific launcher before
    # a workload can silently emit unattributed acquisition events.
    for name in ("ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_RUN_ID"):
        if not os.environ.get(name):
            parser.error(f"Required runner context is missing: {name}")
    identity = str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix="opensre-runner-") as name:
        directory = Path(name)
        profile = directory / "home"
        profile.mkdir()
        (profile / "anonymous_id").write_text(identity, encoding="utf-8")
        write_context(directory, identity, is_test=args.test)
        stopped = threading.Event()

        def renew() -> None:
            while not stopped.wait(120):
                try:
                    write_context(directory, identity, is_test=args.test)
                except Exception:
                    # Never include token-bearing responses in diagnostics.
                    print(
                        "Runner identity renewal failed; expired evidence cannot be verified.",
                        file=sys.stderr,
                    )

        worker = threading.Thread(target=renew, daemon=True)
        worker.start()
        environment = dict(os.environ)
        environment.update(
            {
                OPENSRE_HOME_ENV: str(profile),
                WIZARD_STORE_PATH_ENV: str(profile / "opensre.json"),
                ANALYTICS_EXECUTION_CONTEXT_ENV: str(
                    directory / Path(ANALYTICS_EXECUTION_CONTEXT_PATH).name
                ),
            }
        )
        # Workloads receive only the scoped short-lived token, never authority to
        # mint fresh GitHub tokens for arbitrary audiences.
        environment.pop("ACTIONS_ID_TOKEN_REQUEST_TOKEN", None)
        environment.pop("ACTIONS_ID_TOKEN_REQUEST_URL", None)
        invocation = docker_command(args.docker, directory, command) if args.docker else command
        result = None
        try:
            result = subprocess.run(invocation, env=environment, check=False)
            if args.require_delivery and not (profile / "installed").is_file():
                raise RuntimeError("Runner installation event was not acknowledged")
            return result.returncode
        finally:
            stopped.set()
            worker.join(timeout=20)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(
                json.dumps(
                    {
                        "analytics_id": identity,
                        "runner_run_id": os.environ["GITHUB_RUN_ID"],
                        "runner_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                        "runner_job": os.environ.get("GITHUB_JOB"),
                        "runner_sha": os.environ.get("GITHUB_SHA"),
                        "image": args.docker,
                        "is_test": args.test,
                        "exit_code": result.returncode if result else None,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )


if __name__ == "__main__":
    raise SystemExit(main())
