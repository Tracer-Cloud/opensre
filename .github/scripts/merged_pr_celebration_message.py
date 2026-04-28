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

# All verified HTTP 200. Weighted toward "" so not every PR gets a GIF (keeps it fresh).
gif_blocks: list[str] = [
    "",
    "",
    "\n\n![](https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/g9582DNuQppxC/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/artj92V8o75VPL7AeQ/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/kyLYXonQYYfwYDIeZl/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/xT9IgG50Lg7russbCY/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/zGnnFpOB6s5oNa5VlZ/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/11sBLVxNs7v6WA/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/Swx36wwSsU49HAnIhC/giphy.gif)",
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
