"""Write celebrate-merge PR comment body to comment.md (run from Actions after merge)."""

from __future__ import annotations

import os
import random

discord = os.environ["DISCORD_INVITE_URL"]
contributor = os.environ["CONTRIBUTOR_LOGIN"]

templates: list[str] = [
    (
        f"🎉 **MERGED!** @{contributor} just shipped something. "
        "The diff gods are pleased. 🙌"
    ),
    (
        f"🚀 **Houston, we have a merge.** @{contributor} your PR is in orbit. "
        "Thanks for launching this one!"
    ),
    (
        f"💜 **One more reason the project grows.** Thanks @{contributor} — "
        "your contribution just landed!"
    ),
    (
        f"🎊 **Achievement unlocked: PR Merged.** @{contributor} passed code review, "
        "survived CI, and shipped. Respect. 🤝"
    ),
    (
        f"🔥 **Another one.** @{contributor} said \"here's a PR\" and maintainers said "
        "\"ship it\". That's how it's done."
    ),
    (
        f"🧑‍💻 **@{contributor} has entered the contributor hall of fame.** "
        "Merged. Done. Shipped. Go touch grass (then come back with another PR). 🌱"
    ),
    (
        f"🎯 **Bullseye.** @{contributor} opened a PR, kept the vibes clean, "
        "and got it merged. Absolute cinema. 🎬"
    ),
    (
        f"⚡ **LGTM → Merged.** @{contributor}, your work is in. "
        "Every commit counts — thank you for this one."
    ),
    # new additions
    (
        f"😤 **@{contributor} said \"I will fix this\" and then actually fixed it.** "
        "Not many people do that. You did. Hall of fame behavior."
    ),
    (
        f"🍕 **Hot take:** great PRs and great pizza have the same energy — "
        f"crispy edges, no unnecessary toppings, delivered on time. "
        f"@{contributor} understood the assignment. 🔥"
    ),
    (
        f"🌊 **The PR was opened. The review happened. The merge commit exists.** "
        f"@{contributor} — you are now permanently woven into git history. "
        "No take-backs. No refunds. We love you. 😄"
    ),
    (
        f"🤖 **CI ran. Tests passed. Linter didn't scream. Reviewer typed LGTM.** "
        f"@{contributor}, every machine in this pipeline just gave you a standing ovation. 🖥️✨"
    ),
    (
        f"🧠 **@{contributor} opened a PR.** "
        "Maintainers feared them. Linters respected them. CI genuflected. "
        "It merged. This is not a drill. 🚨"
    ),
    (
        f"😭 **Clear commit message. Green tests. Kind review.** "
        f"@{contributor}, stop making the rest of us look bad. "
        "We're begging. This is too perfect."
    ),
    (
        f"🐸 **Rebase? Handled. Conflicts? Squashed. CI? Fully vibing.** "
        f"@{contributor} touched the untouchable and lived. "
        "Main branch bows in respect. 🫡"
    ),
    (
        f"🏆 **@{contributor} did not come to play.** "
        "Came to open a PR, survive the review gauntlet, and merge clean. "
        "Textbook execution. Retire the jersey. 🎽"
    ),
    (
        f"🎲 **Researchers are baffled.** A PR appeared, got reviewed without drama, "
        f"and merged with zero incidents. @{contributor} has broken the known laws of open source. "
        "Send help. 🔬"
    ),
    (
        f"🌮 **@{contributor}'s PR:** showed up unannounced, immediately improved everything, "
        "left zero bugs, and asked for nothing in return. "
        "Just like a perfect taco. 🌮🌮🌮"
    ),
    (
        f"🐉 **Legend says** each merged PR brings you one step closer to ascending. "
        f"@{contributor} is getting dangerously close to the final form. "
        "We'll miss them when they're gone. 🌤️"
    ),
    (
        f"🛸 **Aliens monitoring our repo** just saw @{contributor}'s PR merge clean "
        "and updated their threat assessment to: "
        "*do not engage — this civilization has too many good contributors*. 👽"
    ),
    (
        f"🎻 **A poet once wrote:** \"The diff was clean, the tests did pass, "
        f"the reviewer wept.\" "
        f"That poem was about @{contributor}'s PR. It was always about this PR. 🥹"
    ),
    (
        f"🍵 **@{contributor} made tea. Opened a PR. Merged before the tea cooled.** "
        "This is what elite performance looks like. No notes. Literally no notes. ☕"
    ),
    (
        f"🏄 **Some PRs get stuck in review purgatory for six weeks.** "
        f"@{contributor}'s PR looked at the queue, said \"not today\", "
        "and merged like it owned the place. 🌊"
    ),
    (
        f"💼 **Interviewer:** describe a time you shipped something with real impact.\n\n"
        f"**@{contributor}:** *silently points at this PR*\n\n"
        "**Interviewer:** ...you're hired. Start Monday. 🤝"
    )
]

# GIFs are repo-hosted under .github/assets/celebrations/ so GitHub's own CDN serves them.
_base = "https://raw.githubusercontent.com/Tracer-Cloud/opensre/main/.github/assets/celebrations"
gif_blocks: list[str] = [
    f"\n\n![]({_base}/party.gif)",
    f"\n\n![]({_base}/celebrate.gif)",
    f"\n\n![]({_base}/ship.gif)",
    f"\n\n![]({_base}/shipped.gif)",
    f"\n\n![]({_base}/fireworks.gif)",
    f"\n\n![]({_base}/woohoo.gif)",
    f"\n\n![]({_base}/office-celebrate.gif)",
    f"\n\n![]({_base}/merge-celebrate-1.gif)",
    f"\n\n![]({_base}/merge-celebrate-2.gif)",
    f"\n\n![]({_base}/merge-celebrate-3.gif)",
]

head = random.choice(templates) + random.choice(gif_blocks)
footer = (
    "---\n\n"
    f"👋 **Join us on [Discord - OpenSRE]({discord})** : hang out, contribute, "
    "or hunt for features and issues. Everyone's welcome."
)
body = f"{head}\n\n{footer}"

with open("comment.md", "w", encoding="utf-8") as fh:
    fh.write(body)
