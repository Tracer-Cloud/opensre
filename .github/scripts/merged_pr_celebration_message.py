"""Write celebrate-merge PR comment body to comment.md (run from Actions after merge)."""

from __future__ import annotations

import os
import random

discord = os.environ["DISCORD_INVITE_URL"]
contributor = os.environ["CONTRIBUTOR_LOGIN"]

templates: list[str] = [
    (
        f"🎉 **Merged — thanks @{contributor}!** Your change is in; "
        "appreciate you taking the time to contribute."
    ),
    (
        f"✨ **Shipped.** Thank you @{contributor} — this PR is merged and "
        "helps everyone using OpenSRE."
    ),
    (
        f"🚀 **Land ho.** @{contributor}, congrats on getting this merged; "
        "reviews and CI did their job."
    ),
    (
        f"💜 **Thank you @{contributor}.** Another contribution landed — "
        "maintainers and users appreciate it."
    ),
    (
        f"🎊 **Merged.** @{contributor}, nice work sticking through review; "
        "glad this is on main."
    ),
    (
        f"👏 **Thanks @{contributor}!** Your PR merged — documentation, fixes, "
        "and features all count."
    ),
    (
        f"✅ **On main.** @{contributor}, appreciated — every merged PR moves "
        "the project forward."
    ),
]

gif_blocks: list[str] = [
    "",
    "\n\n![](https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif)",
    "\n\n![](https://media.giphy.com/media/3oz8xAFtuv5nhAfd0I/giphy.gif)",
]

head = random.choice(templates) + random.choice(gif_blocks)
footer = (
    "---\n\n"
    f"💬 **Community:** [**Discord — OpenSRE**]({discord}) (`#contribute`) — "
    "questions, coordination, and roadmap chatter welcome anytime."
)
body = f"{head}\n\n{footer}"

with open("comment.md", "w", encoding="utf-8") as fh:
    fh.write(body)
