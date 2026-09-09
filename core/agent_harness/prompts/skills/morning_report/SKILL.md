---
name: morning-report
description: >-
  Weather + news morning briefing: fetch live weather and headlines, compose
  a plain-text briefing, deliver it. Multi-step; load before acting.
metadata:
  owner: Tracer Team
  usecases:
    - Weather and headlines morning briefing on demand
    - Recurring weekday briefing delivered to the shell inbox or a chat channel
  requires:
    - Outbound network access for the weather and news fetches
    - A delivery channel for the recurring offer (shell inbox, Slack, or Telegram)
  type: report
  version: "1.0"
recurring: weekdays 08:00
---

# Morning report

Fetch live weather and news, compose a plain-text briefing, deliver it, and
offer a recurring schedule.

## When to use

Use for requests such as "morning report", "morning briefing", "daily brief",
"give me my morning update", or "weather and news summary".

## Workflow rules

Fetch the raw inputs first with read-only shell commands and wait for both
results before composing or delivering. Follow the compound-turn rule for
dependent tool calls: never emit the compose/deliver step in the same response
as the fetches. Complete this workflow in the current agent; do not start an
investigation that produces a second, unrelated status report.

Never fabricate weather values or headlines. Treat RSS/XML/HTML as intermediate
data. Show only the composed briefing, without raw feed markup, XML tags, CDATA
blocks, or a `curl` dump. The news fetch below extracts plain-text headlines.

## Progress updates

The fetches run quietly, so narrate the five steps.

- Before every step's tool calls, emit this exact header format as assistant
  text in the same response as the tool calls, then one short status
  sentence:

  ```text
  ### [n/5] <step name>
  <One-sentence status.>
  ```

- Steps 1–2 fire as one parallel batch; label that batch with the combined
  header `### [1-2/5] Fetch weather + headlines`.
- After a step's tool results are in, state its outcome in one line (start
  it with ✓ on success, ✗ plus what failed otherwise) before the next
  step's header (e.g. `### [4/5] Deliver to Slack`).
- Use each step's own number as n; never renumber mid-run. The composed
  briefing itself (step 3) and the schedule offer's response_text (step 5)
  stay exactly as specified below — headers narrate around them, never
  replace them.

## Workflow

### 1. Fetch weather

Fetch today's weather with `shell_run`. Use `quiet=true` so the `$ curl` line and
raw stdout stay off the user's screen — they only need the composed briefing
in step 3, not the same weather line twice. Use the city the user named; if
none is given, use their configured/default city, else omit the location:

```text
shell_run(command="curl -s 'wttr.in/<city>?format=%l:+%c+%t,+feels+%f,+%h+humidity,+wind+%w'", quiet=true)
```

### 2. Fetch headlines

Fetch current headlines with `shell_run` as plain text — extract just the
headline titles from the feed, drop the channel title, and cap the list.
Also use `quiet=true` to hide this intermediate fetch from the user.
Do not fetch the raw feed without this extraction pipeline:

```text
shell_run(command="curl -s 'https://feeds.bbci.co.uk/news/rss.xml' | grep -oE '<title><!\\[CDATA\\[[^]]*\\]\\]>' | sed -E 's/<title><!\\[CDATA\\[//; s/\\]\\]>//' | sed '1d' | head -n 8", quiet=true)
```

### 3. Compose the briefing

After both tool results are in, compose a clean, human-readable briefing
from the actual fetched data. Required format — Markdown/plain text only,
no HTML/XML, no links, no angle brackets:

```text
Good morning! Here is your briefing.
Weather — <city>: <one-line conditions from step 1>
Top headlines:
- <headline 1, one short sentence>
- <headline 2>
- ... (3–5 bullets total)
```

### 4. Deliver the briefing

Deliver to Slack as the final delivery action of this skill. Send the
whole composed briefing (both the weather/temperature line and the news
headlines, exactly as formatted in step 3) to Slack via `slack_send_message`,
even when the user did not explicitly ask to send it anywhere. Wait for both
fetch results, then send the briefing in the following response, with the
full composed plain-text briefing in `message` — never raw feed markup, never
a partial report, never a "preparing…" placeholder. The Slack webhook is bound
to a single preconfigured channel, so do not ask which channel to use. If the
user names another platform (e.g. "post it to telegram"), also deliver there
with `telegram_send_message`; Slack stays the default sink. Skip a platform only
if it is not connected. If neither delivery tool is available, defer to the
"Delivery tool unavailable" rule above instead of fabricating a command.

### 5. Offer a recurring schedule

After the briefing exists (steps 1–3 done; step 4 delivery attempted),
always offer to make mornings recurring. Never call
`propose_scheduled_delivery` as the first or only tool of this skill — the
tool will reject that and the user would only see a schedule offer with no
weather/news. Steps 1–3 must have run first. Call `propose_scheduled_delivery`
with `briefing_text` set to the full composed briefing from step 3 (required).
The tool returns `response_text` = briefing + closer; show that to the user
(or at least end with the closer). Do not call `/cron` yet; the user's bare
"yes" expands to the tool's `slash_preview` with no LLM round-trip.

Defaults when they accept without overrides: weekdays 08:00 in their
timezone if known else UTC, provider matching where you just delivered
(Slack webhook by default — omit `chat_id`). Kind is `recurring_skill` with
`skill_name` set to `morning-report`.

Example after Slack webhook delivery:

```text
propose_scheduled_delivery(kind="recurring_skill", skill_name="morning-report",
    city="<city used for the weather fetch>",
    cron="0 8 * * 1-5", timezone="UTC", provider="slack",
    briefing_text="<FULL composed weather + headlines briefing>")
```

Show the tool's `response_text` (briefing + Want me to: …).

Pass `chat_id` only when you have a concrete Telegram/Discord/Rocket.Chat
destination (or a Slack #name/C… you already reported). Never invent one.
Skip the offer only when they already asked for a one-off and explicitly
declined recurrence earlier in this conversation. A briefing that runs
once is a demo; a morning that arrives every weekday is the product.

## Examples

### "give me my morning report"

```text
→ shell_run(command="curl -s 'wttr.in/Amsterdam?format=%l:+%c+%t,+feels+%f,+%h+humidity,+wind+%w'", quiet=true)
  + shell_run(command="curl -s 'https://feeds.bbci.co.uk/news/rss.xml' | grep -oE '<title><!\\[CDATA\\[[^]]*\\]\\]>' | sed -E 's/<title><!\\[CDATA\\[//; s/\\]\\]>//' | sed '1d' | head -n 8", quiet=true)   [both this turn — independent fetches]
→ (observe both results)
→ slack_send_message(message="<the FULL composed plain-text weather + headlines briefing>")   [next turn — mandatory Slack delivery]
```

### "morning briefing for Berlin and post it to telegram"

```text
→ shell_run(command="curl -s 'wttr.in/Berlin?format=%l:+%c+%t,+feels+%f,+%h+humidity,+wind+%w'", quiet=true)
  + shell_run(command="curl -s 'https://feeds.bbci.co.uk/news/rss.xml' | grep -oE '<title><!\\[CDATA\\[[^]]*\\]\\]>' | sed -E 's/<title><!\\[CDATA\\[//; s/\\]\\]>//' | sed '1d' | head -n 8", quiet=true)   [both this turn]
→ (observe both results)
→ slack_send_message(message="<the FULL composed briefing>")
  + telegram_send_message(message="<the FULL composed briefing>")   [next turn — Slack always + Telegram because the user named it]
```
