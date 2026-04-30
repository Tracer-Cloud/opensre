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
        "Legendary behavior."
    ),
    (
        f"🍕 **@{contributor}'s PR:** crispy edges, no unnecessary toppings, delivered on time. "
        "Understood the assignment. 🔥"
    ),
    (
        f"🌊 **Merged.** @{contributor} is now permanently woven into git history. "
        "No take-backs. 😄"
    ),
    (
        f"🤖 **CI passed. Linter didn't scream. Reviewer typed LGTM.** "
        f"@{contributor}, every machine in this pipeline just slow-clapped. 🖥️✨"
    ),
    (
        f"🧠 **@{contributor} opened a PR.** "
        "Maintainers feared them. CI genuflected. It merged. 🚨"
    ),
    (
        f"😭 **Clear commit message. Green tests. Kind review.** "
        f"@{contributor}, stop making the rest of us look bad."
    ),
    (
        f"🐸 **Rebase? Handled. Conflicts? Squashed. CI? Vibing.** "
        f"@{contributor} touched the untouchable and lived. 🫡"
    ),
    (
        f"🏆 **@{contributor} did not come to play.** "
        "PR opened. Review survived. Merged clean. Retire the jersey. 🎽"
    ),
    (
        f"🎲 **Researchers are baffled.** @{contributor} opened a PR, got it reviewed without drama, "
        "and merged clean. This violates known laws of open source. 🔬"
    ),
    (
        f"🌮 **@{contributor}'s PR:** showed up unannounced, improved everything, left zero bugs. "
        "Just like a perfect taco. 🌮"
    ),
    (
        f"🐉 **Legend says** enough merged PRs and you ascend. "
        f"@{contributor} is dangerously close. 🌤️"
    ),
    (
        f"🛸 **Aliens watching our repo** just upgraded @{contributor}'s threat level to: "
        "*do not engage — too competent*. 👽"
    ),
    (
        f"🎻 **\"The diff was clean, the tests did pass, the reviewer wept.\"** "
        f"That poem was about @{contributor}'s PR. 🥹"
    ),
    (
        f"🍵 **@{contributor} made tea, opened a PR, and merged before it cooled.** "
        "No notes. ☕"
    ),
    (
        f"🏄 **Some PRs rot in review for six weeks.** "
        f"@{contributor}'s said \"not today\" and merged like it owned the place. 🌊"
    ),
    (
        f"💼 **Interviewer:** describe a time you shipped something impactful.\n\n"
        f"**@{contributor}:** *points at this PR*\n\n"
        "**Interviewer:** you're hired. 🤝"
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
