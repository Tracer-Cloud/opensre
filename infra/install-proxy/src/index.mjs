const SHELL_SCRIPT_URL =
  "https://raw.githubusercontent.com/Tracer-Cloud/opensre/main/install.sh";
const POWERSHELL_SCRIPT_URL =
  "https://raw.githubusercontent.com/Tracer-Cloud/opensre/main/install.ps1";

function requestedShell(request) {
  const url = new URL(request.url);
  const shell = (url.searchParams.get("shell") || "").trim().toLowerCase();

  if (["powershell", "pwsh", "ps1", "windows"].includes(shell)) {
    return "powershell";
  }

  if (["bash", "sh", "zsh", "unix", "linux", "macos"].includes(shell)) {
    return "sh";
  }

  const userAgent = (request.headers.get("user-agent") || "").toLowerCase();
  if (userAgent.includes("powershell") || userAgent.includes("pwsh")) {
    return "powershell";
  }

  return "sh";
}

function targetScriptUrl(request) {
  const url = new URL(request.url);

  if (url.pathname === "/install.ps1") {
    return POWERSHELL_SCRIPT_URL;
  }

  if (url.pathname === "/install.sh") {
    return SHELL_SCRIPT_URL;
  }

  return requestedShell(request) === "powershell" ? POWERSHELL_SCRIPT_URL : SHELL_SCRIPT_URL;
}

function contentTypeFor(request) {
  return targetScriptUrl(request) === POWERSHELL_SCRIPT_URL
    ? "text/plain; charset=utf-8"
    : "application/x-sh; charset=utf-8";
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (!["/", "/install.sh", "/install.ps1"].includes(url.pathname)) {
      return new Response("Not found", {
        status: 404,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    const upstream = await fetch(targetScriptUrl(request), {
      method: request.method,
      headers: {
        "User-Agent": "opensre-install-proxy",
      },
    });

    const headers = new Headers(upstream.headers);
    headers.set("cache-control", "no-store");
    headers.set("content-type", contentTypeFor(request));

    return new Response(upstream.body, {
      status: upstream.status,
      headers,
    });
  },
};
