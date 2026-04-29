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
    f"\n\n![]({_base}/winner.gif)",
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
