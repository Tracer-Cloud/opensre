# Legacy fleet deprecation decision

The maintainer direction for #6809 is to deprecate the legacy external-agent DX
surface. This change starts the notice period; it does not remove commands,
collectors, or saved data. The user-facing migration reference remains at
`docs/platform/fleet.mdx` and its existing URL.

## Adoption evidence and limits

Production adoption has **not** been verified. This development environment has
no configured production analytics credentials or dataset, and repository code
and tests are not evidence of user demand. The decision follows the maintainer's
explicit deprecation request, rather than an assertion that usage is zero.

Existing CLI instrumentation can emit `cli_command_opensre_fleet_list`,
`cli_command_opensre_fleet_scan`, and the corresponding register/forget/watch
events (`infrastructure/analytics/events.py:cli_command_event_name`). Those paths
show what can be measured, not that any events have been received. Before naming
a removal version, maintainers should query the available production dataset
for distinct fleet users and repeat usage over the preceding 30 and 90 days,
inspect retained versions and opt-out/coverage limits, and check support requests
for external-agent monitoring requirements. If collection is insufficient,
record adoption as unknown and use the notice period to gather user feedback.

## Scope and native coverage

The migration guide maps native needs to `/status`, `/sessions`, `/tasks`,
`/cancel`, `/loops`, `/cron`, `/cost`, `/credits`, and `/account usage`.
The remaining native visibility gap is a combined cross-session activity view;
external-process tracing, branch claims, and the fleet bus have no native
equivalent. No PID-to-session redirects or extra accounting ledger are added.
Measured tokens, estimated provider cost, and billed hosted credits stay separate.

## Stages and related work

- This PR adds warnings, labels command discovery as deprecated, removes feature
  promotion, and documents data preservation. Commands, JSON schemas, samplers,
  and persisted formats retain their existing behavior.
- A later release must announce a removal version after the adoption assessment
  and migration feedback. No removal date is promised here.
- A separate removal PR should remove command wiring and collectors, retain the
  migration URL, and leave `agents.jsonl`, `agents.yaml`, and
  `branch_claims.jsonl` on disk. Do not migrate these into native or billing stores.
- #6774 was closed without merging. Its proposed `/fleet list` and `/fleet ls`
  expansion (#6696) is superseded by deprecation; do not revive it as native
  activity monitoring. The retained bare `/fleet` dashboard and CLI
  `opensre fleet list` remain available during the notice period.

Budgets remain stored but unenforced. The conflicts command has no write-event
collector; its empty report is not evidence of safety. Deprecation does not
complete either feature.
