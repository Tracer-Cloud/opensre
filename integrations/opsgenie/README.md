# Opsgenie sunset plan

Atlassian ends Opsgenie access on **April 5, 2027**. New sales and signups ended
on June 4, 2025. Sources: [official timeline](https://www.atlassian.com/software/opsgenie/pricing)
and [migration resources](https://www.atlassian.com/software/opsgenie/migration).

## Maintenance during migration

Keep the existing classifier, configuration, verifier, client, tools, discovery,
and regression tests available while existing users migrate. Limit work to
security, reliability, compatibility fixes, and migration guidance. Do not expand
Opsgenie capabilities or add a new onboarding flow.

Keep the [user migration checklist](../../docs/integrations/incidents/opsgenie.mdx)
and integration directory notice current if Atlassian changes the timeline.

## Removal follow-up

Schedule removal for a release on or after April 5, 2027, after rechecking the
official timeline. Announce the release and removal scope before shipping it.
In that follow-up, remove Opsgenie-specific registration, discovery, configuration,
tools, docs navigation, and tests together; preserve shared incident infrastructure.
Retain guidance for removing stored records and credentials, and ensure generic
integration record removal still works after the provider is unregistered.

This plan does not add a date-triggered runtime cutoff. Provider shutdown and
removal of OpenSRE support are separate events; migration must finish before
Atlassian's shutdown even if OpenSRE removal ships later.
