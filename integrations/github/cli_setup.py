"""GitHub browser sign-in, connection options, and verified setup summary."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, cast

import questionary

from infrastructure.terminal.theme import ANSI_BOLD, ANSI_DIM, ANSI_RESET, DEVICE_CODE_ANSI
from integrations.setup import (
    TerminalSetupUI,
)
from integrations.setup import (
    die as _die,
)
from integrations.setup import (
    prompt_value as _p,
)
from integrations.setup import (
    select as _select,
)

if TYPE_CHECKING:
    from integrations.github.mcp import GitHubMcpDisplayDetailLevel

_B = ANSI_BOLD
_R = ANSI_RESET
_DIM = ANSI_DIM


def _prompt_github_repo_report_level() -> GitHubMcpDisplayDetailLevel:
    """Ask how much repository access detail to print after a successful validation."""

    try:
        sel = _select(
            "How much repository detail should we show?",
            choices=[
                questionary.Choice("Brief (recommended) — no repo names", value="summary"),
                questionary.Choice("Standard — scope summary only", value="standard"),
                questionary.Choice("Expanded — include repo names", value="full"),
            ],
            default="summary",
        )
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    if sel is None:
        return "summary"
    if sel in ("summary", "standard", "full"):
        from integrations.github.mcp import GitHubMcpDisplayDetailLevel as _Detail

        return cast(_Detail, sel)
    return "summary"


def _github_browser_authorize() -> str | None:
    """Run GitHub device-flow browser authorization.

    Returns the access token, or ``None`` when the flow is unavailable so the
    caller can fall back to manual token entry.
    """
    from integrations.github.mcp_oauth import (
        GitHubDeviceCode,
        GitHubDeviceFlowError,
        authorize_github_via_device_flow,
    )

    def _show(code: GitHubDeviceCode) -> None:
        print()
        print(f"  1. Your browser will open {code.verification_uri}")
        print("     (if it doesn't open automatically, visit that URL yourself).")
        print(
            f"  2. Enter this one-time code when GitHub asks: {DEVICE_CODE_ANSI}{code.user_code}{_R}"
        )
        print("  3. Approve the request for OpenSRE.")
        print()
        print(
            f"  {_DIM}Waiting for approval (up to {code.expires_in // 60} minutes; Ctrl+C to cancel)…{_R}"
        )

    print()
    print("  Sign in to GitHub in your browser (device authorization):")
    print(f"  {_DIM}Requesting a one-time code from GitHub…{_R}")
    try:
        token = authorize_github_via_device_flow(on_prompt=_show)
    except GitHubDeviceFlowError as err:
        print(f"  Browser authorization unavailable: {err}", file=sys.stderr)
        return None
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    except Exception as err:  # network/transport issues
        print(f"  Browser authorization failed: {err}", file=sys.stderr)
        return None
    print(f"  {_B}Authorized.{_R} I'll check your repository access before saving.")
    return token.access_token


def _github_browser_auth_token() -> str:
    """Authorize in the browser, falling back to manual token entry."""
    token = _github_browser_authorize()
    if token:
        return token
    print("  Falling back to manual token entry.")
    return _manual_github_token()


def _manual_github_token() -> str:
    """Explain token creation before asking for a secret."""
    print("  Open https://github.com/settings/personal-access-tokens/new")
    print(
        "  Name it OpenSRE, choose the owner and repositories you want to connect, and set an expiry."
    )
    print("  Enable read access to Contents, Metadata, Issues, Pull requests, and Actions.")
    print(
        "  Generate the token, copy it, and paste it below. Your organization may need to approve it."
    )
    return _p("GitHub PAT / auth token", secret=True)


def _setup_github_auth_token(mode: str) -> str:
    """Resolve a GitHub MCP auth token, offering browser sign-in for remote modes."""
    if mode == "stdio":
        return _p(
            "GitHub PAT / auth token (optional if the server authenticates upstream)",
            secret=True,
        )

    auth_method = _select(
        "How do you want to connect OpenSRE to GitHub?",
        choices=[
            questionary.Choice(
                "Sign in with GitHub in your browser (opens a page, enter a one-time code)",
                value="browser",
            ),
            questionary.Choice("Paste a personal access token (PAT)", value="token"),
            questionary.Choice("Skip — the MCP server authenticates upstream", value="none"),
        ],
        default="browser",
    )
    if auth_method is None:
        print("\nAborted.")
        sys.exit(1)
    if auth_method == "none":
        return ""
    if auth_method == "browser":
        return _github_browser_auth_token()
    return _manual_github_token()


def _github_advanced_setup(credentials: dict[str, Any]) -> tuple[str, str]:
    """Prompt the advanced GitHub MCP knobs and return (repo_view, repo_visibility).

    Mutates ``credentials`` in place with mode/url/command/args/auth_token/toolsets.
    """
    from integrations.github.mcp import (
        DEFAULT_GITHUB_MCP_TOOLSETS,
        DEFAULT_GITHUB_MCP_URL,
    )

    # Transport is fixed to Streamable HTTP. In practice it is the only mode anyone
    # selects, and SSE/stdio are deprecated for the hosted GitHub MCP server. The
    # transport prompt was removed on purpose — do NOT reintroduce a transport
    # selection or a stdio branch here.
    mode = "streamable-http"
    credentials["mode"] = mode
    url = _p("MCP URL", default=DEFAULT_GITHUB_MCP_URL)
    if not url:
        _die("url is required for remote MCP modes.")
    credentials["url"] = url
    credentials["auth_token"] = _setup_github_auth_token(mode)
    toolsets = _p("Toolsets", default=",".join(DEFAULT_GITHUB_MCP_TOOLSETS))
    credentials["toolsets"] = [part.strip() for part in toolsets.split(",") if part.strip()]

    repo_view = _select(
        "Which repository view should we use to verify access?",
        choices=[
            questionary.Choice("Auto (recommended)", value="auto"),
            questionary.Choice("Your repositories", value="user"),
            questionary.Choice("Accessible repositories", value="accessible"),
            questionary.Choice("Starred repositories", value="starred"),
            questionary.Choice("Search: user:<your_login>", value="search_user"),
        ],
        default="auto",
    )
    if repo_view is None:
        print("\nAborted.")
        sys.exit(1)
    repo_visibility = _select(
        "Filter repositories by visibility (best-effort)",
        choices=[
            questionary.Choice("Any (recommended)", value="any"),
            questionary.Choice("Public only", value="public"),
            questionary.Choice("Private only", value="private"),
        ],
        default="any",
    )
    if repo_visibility is None:
        print("\nAborted.")
        sys.exit(1)
    return repo_view, repo_visibility


def _use_workspace_github(ui: TerminalSetupUI) -> str | None:
    """Offer the workspace's GitHub connection; return its login when the user takes it.

    Returns ``None`` (fall through to sign-in) when signed out, the webapp has no
    GitHub connection, the sync fails, or the user prefers a separate sign-in;
    otherwise the login, which is "" when the webapp did not report one.
    """
    from integrations.github.workspace_sync import (
        describe_github_sync,
        fetch_workspace_github,
        sync_workspace_github,
    )

    payload = fetch_workspace_github()
    if payload is None or payload.get("connected") is not True:
        return None
    username = str(payload.get("username") or "").strip()
    label = f"@{username}" if username else "your workspace account"
    choice = _select(
        "Your OpenSRE workspace already has GitHub connected. Use it here?",
        choices=[
            questionary.Choice(
                f"Use the workspace connection ({label}) — recommended", value="use"
            ),
            questionary.Choice("Sign in separately on this machine", value="separate"),
        ],
        default="use",
    )
    if choice is None:
        print("\nAborted.")
        sys.exit(1)
    if choice != "use":
        return None
    result = sync_workspace_github(replace_manual=True)
    ui.say(describe_github_sync(result))
    if result.status not in {"connected", "updated", "unchanged"}:
        return None
    return result.username or username


def _offer_workspace_share(ui: TerminalSetupUI, auth_token: str) -> None:
    """Offer to share a verified connection with the workspace's hosted agent.

    Asked only when signed in and the workspace has no GitHub yet, so an
    existing workspace connection is never replaced from a laptop.
    """
    from integrations.github.workspace_sync import (
        fetch_workspace_github,
        share_github_with_workspace,
    )

    if not auth_token:
        return
    payload = fetch_workspace_github()
    if payload is None or payload.get("connected") is True:
        return
    choice = _select(
        "Share this GitHub connection with your OpenSRE workspace? The hosted agent "
        "(Slack, Telegram) and your teammates' CLIs will use it.",
        choices=[
            questionary.Choice("Yes, share it with the workspace", value="share"),
            questionary.Choice("No, keep it on this machine only", value="local"),
        ],
        default="share",
    )
    if choice != "share":
        return
    result = share_github_with_workspace(auth_token)
    if not result.ok:
        ui.say(
            "Could not share GitHub with the workspace. Connect it in the web app "
            "under Settings → GitHub instead."
        )
    elif result.delivered_to_agent:
        ui.say("Shared with your workspace. The hosted agent can use GitHub within a minute.")
    else:
        ui.say(
            "Shared with your workspace. Your hosted agent is not set up yet; it gets "
            "GitHub as soon as it is."
        )


def setup_github() -> str | None:
    """Configure + validate + save the GitHub MCP integration.

    Returns the authenticated GitHub login on success (``None`` if the validated
    result carried no login), so callers like the first-launch gate can propagate
    the username. Recoverable validation failures retry in place; cancellation exits.

    Collection stays custom (browser OAuth + optional repo-scope probes). Persist
    goes through :func:`integrations.setup_flow.apply_setup` so the token lands
    in the keyring and the non-secrets in ``.env``, not just the store.
    """
    import dataclasses

    from integrations.github.mcp import (
        DEFAULT_GITHUB_MCP_MODE,
        DEFAULT_GITHUB_MCP_TOOLSETS,
        DEFAULT_GITHUB_MCP_URL,
        GitHubMcpDisplayDetailLevel,
        GitHubMcpRepoView,
        GitHubMcpRepoVisibilityFilter,
        build_github_mcp_config,
        format_github_mcp_validation_cli_report,
        print_github_mcp_validation_report,
        validate_github_mcp_config,
    )
    from integrations.github.setup import GITHUB_SETUP
    from integrations.setup_flow import apply_setup

    ui = TerminalSetupUI()
    workspace_login = _use_workspace_github(ui)
    if workspace_login is not None:
        return workspace_login
    ui.say("I'll guide you through three steps. Press Ctrl+C at any time to cancel.")
    ui.step("one", "Sign in to GitHub")
    print("  Connect OpenSRE to your GitHub repositories.")
    print(
        "  Sign in in your browser and approve OpenSRE. I'll discover your account and repository access."
    )
    setup_path = _select(
        "How would you like to connect?",
        choices=[
            questionary.Choice("Sign in with GitHub (recommended)", value="recommended"),
            questionary.Choice("Customize connection", value="customize"),
        ],
        default="recommended",
    )
    if setup_path is None:
        print("\nAborted.")
        sys.exit(1)
    customize = setup_path == "customize"

    credentials: dict[str, Any] = {}
    repo_view: str = "auto"
    repo_visibility: str = "any"

    if customize:
        repo_view, repo_visibility = _github_advanced_setup(credentials)
    else:
        credentials["mode"] = DEFAULT_GITHUB_MCP_MODE
        credentials["url"] = DEFAULT_GITHUB_MCP_URL
        credentials["auth_token"] = _github_browser_auth_token()
        credentials["toolsets"] = list(DEFAULT_GITHUB_MCP_TOOLSETS)

    ui.step("two", "Check account and repository access")
    while True:
        print("\n  Validating GitHub MCP integration...")
        mcp_config = build_github_mcp_config(credentials)
        result = validate_github_mcp_config(
            mcp_config,
            repo_view=cast(GitHubMcpRepoView, repo_view),
            repo_visibility=cast(GitHubMcpRepoVisibilityFilter, repo_visibility),
        )
        if result.ok:
            break
        report = format_github_mcp_validation_cli_report(result)
        token = str(credentials.get("auth_token") or "")
        ui.say(report.replace(token, "[redacted]") if token else report)
        ui.say(
            "Check your connection, token permissions, or organization approval, then retry here."
        )
        try:
            action = ui.choose(
                "Next action",
                [
                    ("retry", "Retry the check"),
                    ("auth", "Sign in again"),
                    ("token", "Use a personal access token"),
                    ("cancel", "Cancel"),
                ],
            )
        except (EOFError, KeyboardInterrupt):
            sys.exit(1)
        if action == "auth":
            credentials["auth_token"] = _github_browser_auth_token()
        elif action == "token":
            credentials["auth_token"] = _manual_github_token()
    # The simple path stays concise: identity + tool availability, no repo dump.
    # Only the advanced path offers the verbose repo listing.
    level = (
        _prompt_github_repo_report_level()
        if customize
        else cast(GitHubMcpDisplayDetailLevel, "summary")
    )
    print()
    print_github_mcp_validation_report(result, detail_level=level)

    toolsets = credentials.get("toolsets") or []
    if isinstance(toolsets, str):
        toolsets_value = toolsets
    else:
        toolsets_value = ",".join(str(part).strip() for part in toolsets if str(part).strip())

    # Already verified above (with optional repo-scope probes). Skip the spec's
    # simpler probe so we do not hit the hosted server twice.
    ui.step("three", "Save your verified connection")
    values = {
        "mode": str(credentials.get("mode") or DEFAULT_GITHUB_MCP_MODE),
        "url": str(credentials.get("url") or DEFAULT_GITHUB_MCP_URL),
        "auth_token": str(credentials.get("auth_token") or ""),
        "toolsets": toolsets_value,
        "username": result.authenticated_user or "",
    }
    while True:
        outcome = apply_setup(dataclasses.replace(GITHUB_SETUP, verify=None), values)
        if outcome.ok:
            break
        ui.say("The connection is verified, but saving failed. Check local file permissions.")
        try:
            ui.choose("Next action", [("retry", "Retry saving"), ("cancel", "Cancel")])
        except (EOFError, KeyboardInterrupt):
            sys.exit(1)
    ui.say(
        f"Verified GitHub: @{result.authenticated_user or 'connected account'}; repository access checked. Saved."
    )
    if result.repo_access_count == 0:
        ui.say(
            "No repositories were returned. Check repository access or organization approval before requesting repository work."
        )
    _offer_workspace_share(ui, values["auth_token"])
    return result.authenticated_user
