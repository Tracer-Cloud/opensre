# How surfaces boot

OpenSRE can be reached in several ways — the `opensre` CLI, the interactive
shell, the gateway daemon (chat channels), the gateway's web app, scheduled
`cron` commands, and even a plain Python script embedding the agent. Each of
them starts up by running the same shared setup —
`configure_process(<profile>)` in
[`bootstrap/process.py`](https://github.com/Tracer-Cloud/opensre/blob/main/bootstrap/process.py) — rather than assembling
its own startup sequence. A surface's own code is responsible only for its
channel and UX (rendering to a terminal, replying in chat, serving an HTTP
route, parsing CLI arguments) plus any prerequisites specific to that
surface; some surfaces do a little of their own setup before or after the
shared steps run (see "Where each surface calls it" below).

## What the shared setup does

Startup is a short checklist of setup work that has to happen in the same
order every time:

1. **Load the environment** — read configuration and secrets.
2. **Start error reporting** — so failures later in startup are still
   captured.
3. **Register adapters** — connect the agent to the integrations and tools it
   can use.
4. **Register the scheduler's task runners** — so scheduled work (digests,
   reports, cron jobs) knows how to run.
5. **Log capability warnings** — flag anything the sandbox can't do in this
   environment.
6. **Preload the LLM client modules** — so a long-running process doesn't end
   up mixing old and new versions of those modules after a later code
   change.

Not every surface needs every step. A profile is just a name for "the subset
of this checklist a given surface needs" — the CLI needs far less than the
gateway daemon does. Running the shared setup with a profile performs
exactly those steps, in the order above, and running it again for a surface
that's already started is a harmless no-op.

## The six profiles

| Profile | What it sets up | Used by |
| --- | --- | --- |
| CLI | Just the environment. | The `opensre` command |
| Gateway | Environment, error reporting, adapters, capability warnings, LLM preload. | The gateway daemon (chat channels) |
| Web | Environment, error reporting, adapters. | The gateway's standalone web app |
| Scheduler worker | Environment, error reporting, adapters, scheduler task runners. | The `opensre cron start` daemon |
| Scheduled command | Environment, adapters, scheduler task runners. | One-off CLI commands that create, run, or dispatch scheduled work |
| Embedded | Environment, adapters. | Driving the agent from another Python program |

## Where each surface calls it

- **CLI** — [`surfaces/cli/startup.py`](https://github.com/Tracer-Cloud/opensre/blob/main/surfaces/cli/startup.py) runs the
  CLI profile first, then handles CLI-only setup: its own error reporting
  (tolerant of a missing dependency during `opensre update`), terminal output
  styling, and keyboard-interrupt handling. [`main.py`](https://github.com/Tracer-Cloud/opensre/blob/main/main.py), the
  module-level entry point (`python main.py`), delegates straight into this
  same CLI path — it does not use the embedded profile. (The embedded
  profile only appears in `main.py`'s docstring, as an illustrative example
  for someone embedding the agent in their own script.) The interactive
  shell runs inside this same already-started CLI process, so it doesn't
  start up again.
- **Interactive shell's saved loops** — when a saved prompt loop needs its
  own background scheduler, it starts up with the scheduled-command profile
  in
  [`surfaces/interactive_shell/runtime/loop_scheduler.py`](https://github.com/Tracer-Cloud/opensre/blob/main/surfaces/interactive_shell/runtime/loop_scheduler.py)
  and
  [`surfaces/interactive_shell/command_registry/loops_cmds.py`](https://github.com/Tracer-Cloud/opensre/blob/main/surfaces/interactive_shell/command_registry/loops_cmds.py).
- **Gateway daemon** — [`gateway/core/lifecycle/controller.py`](https://github.com/Tracer-Cloud/opensre/blob/main/gateway/core/lifecycle/controller.py)
  sets up its own logging, readiness state, and credentials first, then runs
  the gateway profile before connecting chat channels and the scheduler.
- **Gateway web app** — [`gateway/web/webapp.py`](https://github.com/Tracer-Cloud/opensre/blob/main/gateway/web/webapp.py)
  runs the web profile as soon as the module loads, so both the in-process
  gateway and a standalone web server have everything they need before
  handling a request.
- **Scheduled/cron CLI commands** — one-off commands like `cron run` in
  [`surfaces/cli/commands/cron.py`](https://github.com/Tracer-Cloud/opensre/blob/main/surfaces/cli/commands/cron.py) and
  [`sentry_digest.py`](https://github.com/Tracer-Cloud/opensre/blob/main/surfaces/cli/commands/sentry_digest.py) run the
  scheduled-command profile.
  [`posthog_report.py`](https://github.com/Tracer-Cloud/opensre/blob/main/surfaces/cli/commands/posthog_report.py) checks
  that the PostHog integration is configured first, then runs the same
  profile. The long-running `cron start` daemon uses the scheduler-worker
  profile instead, since it needs its own error reporting and only needs to
  register task runners once, not on every run.
- **Embedded Python usage** — [`bootstrap/embedded.py`](https://github.com/Tracer-Cloud/opensre/blob/main/bootstrap/embedded.py)
  runs the embedded profile. This is also the pattern to follow if you're
  driving the agent from your own Python script: run
  `configure_process(EMBEDDED_PROFILE)` before your first request.

## Why it works this way

Keeping one shared startup checklist means every surface gets the same
guarantees — nothing is skipped, and nothing runs out of order — no matter
how it's launched. It also means adding or changing a startup step only
needs to happen in one place. For how this fits into OpenSRE's broader
package layout, see [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
