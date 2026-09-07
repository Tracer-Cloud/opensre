#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || {
  printf '%s\n' "Error: install.sh requires bash. Run 'bash install.sh' or pipe it into bash." >&2
  exit 1
}

set -euo pipefail

if [ -t 1 ]; then
  COLOR_RESET=$'\033[0m'
  COLOR_RED=$'\033[31m'
  COLOR_GREEN=$'\033[32m'
  COLOR_YELLOW=$'\033[33m'
  COLOR_GRAY=$'\033[90m'
  SUCCESS_MARK="✓"
else
  COLOR_RESET=""
  COLOR_RED=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
  COLOR_GRAY=""
  SUCCESS_MARK="Success:"
fi

REPO="${OPENSRE_INSTALL_REPO:-Tracer-Cloud/opensre}"
DEFAULT_INSTALL_DIR="${HOME}/.local/bin"
USER_INSTALL_DIR_CANDIDATES="${OPENSRE_USER_INSTALL_DIR_CANDIDATES:-$HOME/.local/bin:$HOME/bin}"
SYSTEM_INSTALL_DIR_CANDIDATES="${OPENSRE_SYSTEM_INSTALL_DIR_CANDIDATES:-/opt/homebrew/bin:/usr/local/bin:/opt/local/bin}"
INSTALL_DIR="${OPENSRE_INSTALL_DIR:-}"
INSTALL_DIR_OVERRIDE=0
INSTALL_CHANNEL="${OPENSRE_INSTALL_CHANNEL:-main}"
INSTALL_CHANNEL_EXPLICIT=0
[ -n "${OPENSRE_INSTALL_CHANNEL:-}" ] && INSTALL_CHANNEL_EXPLICIT=1
MAIN_RELEASE_TAG="${OPENSRE_MAIN_RELEASE_TAG:-main-build}"
BIN_NAME="opensre"
requested_version="${OPENSRE_VERSION:-}"

[ -n "$INSTALL_DIR" ] && INSTALL_DIR_OVERRIDE=1
requested_version="${requested_version#v}"

log() {
  printf '%s\n' "$*"
}

muted() {
  printf '%s%s%s\n' "${COLOR_GRAY:-}" "$*" "${COLOR_RESET:-}"
}

warn() {
  printf '%sWarning:%s %s\n' "${COLOR_YELLOW:-}" "${COLOR_RESET:-}" "$*" >&2
}

die() {
  printf '%sError:%s %s\n' "${COLOR_RED:-}" "${COLOR_RESET:-}" "$*" >&2
  exit 1
}

success() {
  printf '%s%s %s%s\n' "${COLOR_GREEN:-}" "${SUCCESS_MARK:-Success:}" "$*" "${COLOR_RESET:-}"
}

install_verbose() {
  case "${OPENSRE_INSTALL_VERBOSE:-}" in
    1|true|TRUE|yes|YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_interactive_status_terminal() {
  [ -t 2 ] && [ "${TERM:-}" != "dumb" ] && ! install_verbose
}

animate_dots() {
  local label="$1"
  local dot_count=1
  local dots

  while :; do
    case "$dot_count" in
      1) dots="." ;;
      2) dots=".." ;;
      *) dots="..." ;;
    esac
    printf '\r\033[K%s%s%s%s' \
      "${COLOR_GRAY:-}" "$label" "$dots" "${COLOR_RESET:-}" >&2
    dot_count=$((dot_count % 3 + 1))
    sleep 0.4
  done
}

finish_dots() {
  local dots_pid="$1"
  local label="$2"

  kill "$dots_pid" 2>/dev/null || true
  wait "$dots_pid" 2>/dev/null || true
  printf '\r\033[K%s%s...%s\n\033[?25h' \
    "${COLOR_GRAY:-}" "$label" "${COLOR_RESET:-}" >&2
}

run_with_dots() {
  local label="$1"
  shift

  if ! is_interactive_status_terminal; then
    printf '%s%s...%s\n' \
      "${COLOR_GRAY:-}" "$label" "${COLOR_RESET:-}" >&2
    "$@"
    return
  fi

  local dots_pid
  local status

  printf '\033[?25l' >&2
  animate_dots "$label" &
  dots_pid=$!
  trap 'finish_dots "$dots_pid" "$label"; exit 130' INT TERM

  if "$@"; then
    status=0
  else
    status=$?
  fi

  finish_dots "$dots_pid" "$label"
  trap - INT TERM
  return "$status"
}

usage() {
  cat <<'EOF'
Usage: install.sh [--main] [--release] [--version <version>] [--install-dir <path>]

Installs the OpenSRE CLI.

Options:
  --main                Install the latest build published from the main branch (default).
  --release             Install the latest versioned release instead of main.
  --version <version>   Install a specific versioned release (for example 2026.4.29).
  --install-dir <path>  Install into a specific directory.
  -h, --help            Show this help text.

Examples:
  curl -fsSL https://install.opensre.com | bash
  curl -fsSL https://install.opensre.com | bash -s -- --main
  curl -fsSL https://install.opensre.com | bash -s -- --version 2026.4.29
EOF
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --main)
        INSTALL_CHANNEL="main"
        INSTALL_CHANNEL_EXPLICIT=1
        ;;
      --release)
        INSTALL_CHANNEL="release"
        INSTALL_CHANNEL_EXPLICIT=1
        ;;
      --version)
        [ "$#" -ge 2 ] || die "--version requires a value."
        requested_version="${2#v}"
        shift
        ;;
      --install-dir)
        [ "$#" -ge 2 ] || die "--install-dir requires a value."
        INSTALL_DIR="$2"
        INSTALL_DIR_OVERRIDE=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
    shift
  done

  case "$INSTALL_CHANNEL" in
    release|main) ;;
    *)
      die "Unsupported install channel: ${INSTALL_CHANNEL}"
      ;;
  esac

  if [ -n "$requested_version" ] && [ "$INSTALL_CHANNEL" = "main" ] && [ "$INSTALL_CHANNEL_EXPLICIT" -eq 0 ]; then
    INSTALL_CHANNEL="release"
  fi

  if [ "$INSTALL_CHANNEL" = "main" ] && [ -n "$requested_version" ]; then
    die "--version cannot be combined with --main."
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but was not found in PATH."
}

skip_github_cli_install() {
  case "${OPENSRE_SKIP_GH_INSTALL:-}" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

ensure_github_cli() {
  # Soft dependency for github_cli chat tools. Never fails the OpenSRE install.
  if command -v gh >/dev/null 2>&1; then
    if install_verbose; then
      log "GitHub CLI (gh) already on PATH: $(command -v gh)"
    fi
    return 0
  fi

  if skip_github_cli_install; then
    warn "GitHub CLI (gh) is not on PATH; skipped install because OPENSRE_SKIP_GH_INSTALL is set."
    warn "Install from https://cli.github.com/ for OpenSRE GitHub chat tools."
    return 0
  fi

  if command -v brew >/dev/null 2>&1; then
    if run_with_dots "Installing GitHub CLI (gh) for OpenSRE GitHub tools" brew install gh; then
      success "Installed GitHub CLI (gh) via Homebrew"
      return 0
    fi
    warn "Homebrew failed to install gh. Install manually from https://cli.github.com/"
    return 0
  fi

  if command -v apt-get >/dev/null 2>&1; then
    # Soft dependency only — never require sudo for the OpenSRE installer.
    warn "GitHub CLI (gh) is missing and apt is available but auto-install needs sudo."
    warn "Install manually: apt install gh  (or https://cli.github.com/) for OpenSRE GitHub chat tools."
    return 0
  fi

  warn "GitHub CLI (gh) is not on PATH and no supported package manager was found."
  warn "Install from https://cli.github.com/ for OpenSRE GitHub chat tools."
}

require_prerequisites() {
  need_cmd curl
  need_cmd grep
  need_cmd sed
  need_cmd tr
  need_cmd uname
}

CURL_FLAGS=(
  --fail
  --silent
  --show-error
  --location
  --retry 3
  --retry-delay 1
)

download_to() {
  local url="$1"
  local destination="$2"

  curl "${CURL_FLAGS[@]}" -o "$destination" "$url"
}

download_text() {
  local url="$1"

  curl "${CURL_FLAGS[@]}" \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: opensre-install-script" \
    "$url"
}

fetch_release_json() {
  local version="${1:-}"
  local api_url

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    api_url="https://api.github.com/repos/${REPO}/releases/tags/${MAIN_RELEASE_TAG}"
  elif [ -n "$version" ]; then
    api_url="https://api.github.com/repos/${REPO}/releases/tags/v${version}"
  else
    api_url="https://api.github.com/repos/${REPO}/releases/latest"
  fi

  download_text "$api_url"
}

extract_tag_name() {
  local release_json="$1"

  printf '%s\n' "$release_json" | sed -n '/"tag_name"[[:space:]]*:/{
    s/.*"tag_name":[[:space:]]*"v\{0,1\}\([^"]*\)".*/\1/p
    q
  }'
}

release_has_asset() {
  local release_json="$1"
  local asset_name="$2"

  printf '%s' "$release_json" | tr -d '\r\n\t ' | grep -F "\"name\":\"${asset_name}\"" >/dev/null 2>&1
}

build_archive_name() {
  local version="$1"
  local asset_arch="$2"
  local archive_version="$version"

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    archive_version="main"
  fi

  if [ "$platform" = "windows" ]; then
    printf 'opensre_%s_windows-%s.zip\n' "$archive_version" "$asset_arch"
    return
  fi

  printf 'opensre_%s_%s-%s.tar.gz\n' "$archive_version" "$platform" "$asset_arch"
}

path_has_dir() {
  case ":$PATH:" in
    *":$1:"*)
      return 0
      ;;
  esac

  return 1
}

is_candidate_dir_writable() {
  local dir="$1"
  local parent_dir

  if [ -d "$dir" ]; then
    [ -w "$dir" ]
    return
  fi

  parent_dir="${dir%/*}"
  [ -n "$parent_dir" ] || parent_dir="/"
  [ -d "$parent_dir" ] && [ -w "$parent_dir" ]
}

is_python_venv_bin_dir() {
  # Never symlink the release binary into an active virtualenv's bin/.
  # Contributors often have ``.venv/bin`` first on PATH (``uv run`` / ``make``);
  # linking there replaces the editable console script and breaks the checkout.
  local dir="$1"
  local parent=""

  case "$dir" in
    */.venv/bin|*/.venv/Scripts|*/venv/bin|*/venv/Scripts|*/.virtualenv/bin)
      return 0
      ;;
  esac

  parent="${dir%/*}"
  if [ -n "$parent" ] && [ -f "${parent}/pyvenv.cfg" ]; then
    return 0
  fi

  return 1
}

select_writable_path_candidate_from_list() {
  local candidate_list="$1"
  local old_ifs="$IFS"
  local dir

  IFS=':'
  for dir in $candidate_list; do
    case "$dir" in
      /*) ;;
      *) continue ;;
    esac
    if is_python_venv_bin_dir "$dir"; then
      continue
    fi
    if path_has_dir "$dir" && is_candidate_dir_writable "$dir"; then
      printf '%s\n' "$dir"
      IFS="$old_ifs"
      return 0
    fi
  done
  IFS="$old_ifs"

  return 1
}

resolve_install_dir() {
  local existing_bin=""
  local existing_dir=""

  if [ -n "$INSTALL_DIR" ]; then
    return
  fi

  if [ "$platform" = "windows" ]; then
    INSTALL_DIR="$DEFAULT_INSTALL_DIR"
    return
  fi

  if command -v opensre >/dev/null 2>&1; then
    existing_bin="$(command -v opensre || true)"
    existing_dir="${existing_bin%/*}"

    if [ -n "$existing_dir" ] \
      && ! is_python_venv_bin_dir "$existing_dir" \
      && path_has_dir "$existing_dir" \
      && is_candidate_dir_writable "$existing_dir"; then
      INSTALL_DIR="$existing_dir"
      return
    fi
  fi

  if INSTALL_DIR="$(select_writable_path_candidate_from_list "$USER_INSTALL_DIR_CANDIDATES")"; then
    return
  fi

  if INSTALL_DIR="$(select_writable_path_candidate_from_list "$SYSTEM_INSTALL_DIR_CANDIDATES")"; then
    return
  fi

  INSTALL_DIR="$DEFAULT_INSTALL_DIR"
}

ps_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

to_windows_path() {
  local posix_path="$1"

  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$posix_path"
    return
  fi

  die "PowerShell archive extraction requires 'cygpath' when 'unzip' is unavailable."
}

extract_zip() {
  local archive_path="$1"
  local destination_dir="$2"
  local archive_for_ps
  local destination_for_ps

  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$archive_path" -d "$destination_dir"
    return
  fi

  archive_for_ps="$(ps_escape "$(to_windows_path "$archive_path")")"
  destination_for_ps="$(ps_escape "$(to_windows_path "$destination_dir")")"

  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoLogo -NoProfile -NonInteractive -Command \
      "Expand-Archive -LiteralPath '$archive_for_ps' -DestinationPath '$destination_for_ps' -Force" \
      >/dev/null
    return
  fi

  if command -v pwsh >/dev/null 2>&1; then
    pwsh -NoLogo -NoProfile -NonInteractive -Command \
      "Expand-Archive -LiteralPath '$archive_for_ps' -DestinationPath '$destination_for_ps' -Force" \
      >/dev/null
    return
  fi

  die "A zip extractor is required on Windows. Install 'unzip' or run the PowerShell installer."
}

extract_archive() {
  local archive_path="$1"
  local destination_dir="$2"

  if [ "$platform" = "windows" ]; then
    extract_zip "$archive_path" "$destination_dir"
    return
  fi

  need_cmd tar
  tar -xzf "$archive_path" -C "$destination_dir"
}

verify_checksum() {
  local checksum_path="$1"
  local archive_path="$2"
  local archive_dir
  local checksum_name
  local normalized_checksum_path
  local expected
  local actual

  archive_dir="${archive_path%/*}"
  checksum_name="${checksum_path##*/}"
  normalized_checksum_path="${checksum_path}.normalized"

  tr -d '\r' < "$checksum_path" > "$normalized_checksum_path"
  checksum_path="$normalized_checksum_path"
  checksum_name="${checksum_path##*/}"

  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$archive_dir" && sha256sum -c "$checksum_name") >/dev/null \
      || die "Checksum verification failed for '${archive_path##*/}'."
    return
  fi

  if command -v shasum >/dev/null 2>&1; then
    (cd "$archive_dir" && shasum -a 256 -c "$checksum_name") >/dev/null \
      || die "Checksum verification failed for '${archive_path##*/}'."
    return
  fi

  if command -v openssl >/dev/null 2>&1; then
    expected="$(sed -n 's/^\([0-9A-Fa-f]\{64\}\)[[:space:]][[:space:]]*.*/\1/p' "$checksum_path")"
    [ -n "$expected" ] || die "Checksum file '${checksum_name}' is malformed."

    actual="$(openssl dgst -sha256 "$archive_path" | sed 's/^.*= //')"
    [ "$expected" = "$actual" ] || die "Checksum verification failed for '${archive_path##*/}'."
    return
  fi

  warn "No checksum verifier found (sha256sum, shasum, or openssl). Skipping checksum verification."
}

binary_app_root() {
  local binary_path="$1"
  local binary_dir

  binary_dir="${binary_path%/*}"
  if [ -d "${binary_dir}/_internal" ]; then
    printf '%s\n' "$binary_dir"
    return 0
  fi

  return 1
}

install_binary() {
  local source_path="$1"
  local destination_path="$2"

  if command -v install >/dev/null 2>&1; then
    install -m 0755 "$source_path" "$destination_path"
    return
  fi

  cp "$source_path" "$destination_path"
  chmod 0755 "$destination_path" 2>/dev/null || true
}

# Install is two renames with the checks in between: stage the extracted tree
# under INSTALL_DIR, verify and warm it there, then swap it into place. A binary
# that fails its checks never replaces a working install, and nothing is
# copied: macOS caches signature validation per file, so a renamed tree keeps
# what the checks paid for while a copied tree is validated again on the
# user's first launch.

stage_binary() {
  local source_path="$1"
  local app_root=""

  mkdir -p "$INSTALL_DIR"
  if [ "$platform" != "windows" ] && app_root="$(binary_app_root "$source_path")"; then
    stage_binary_app "$app_root"
    return
  fi

  stage_single_binary "$source_path"
}

stage_binary_app() {
  local app_root="$1"
  local staged_dir="${INSTALL_DIR}/.${BIN_NAME}-app.new.$$"

  rm -rf "$staged_dir"
  mv "$app_root" "$staged_dir"
  chmod -R u+rwX,go+rX "$staged_dir" 2>/dev/null || true
  printf '%s\n' "${staged_dir}/${BIN_NAME}"
}

stage_single_binary() {
  local source_path="$1"
  local staged_path="${INSTALL_DIR}/${BIN_NAME}.new.$$"

  install_binary "$source_path" "$staged_path"
  printf '%s\n' "$staged_path"
}

staged_binary_is_app() {
  [ "${1%/*}" != "$INSTALL_DIR" ]
}

activate_staged_binary() {
  local staged_path="$1"
  local destination_path="$2"
  local app_destination_dir="${INSTALL_DIR}/.${BIN_NAME}-app"
  local app_old_dir="${app_destination_dir}.old.$$"

  if ! staged_binary_is_app "$staged_path"; then
    mv -f "$staged_path" "$destination_path"
    return
  fi

  rm -rf "$app_old_dir"
  if [ -e "$app_destination_dir" ]; then
    mv "$app_destination_dir" "$app_old_dir"
  fi
  mv "${staged_path%/*}" "$app_destination_dir"
  rm -rf "$app_old_dir"

  rm -f "$destination_path"
  ln -s "$app_destination_dir/${BIN_NAME}" "$destination_path"
}

discard_staged_binary() {
  local staged_path="$1"

  if staged_binary_is_app "$staged_path"; then
    rm -rf "${staged_path%/*}"
  else
    rm -f "$staged_path"
  fi
}

download_and_verify_checksum() {
  local checksum_url="$1"
  local checksum_path="$2"
  local archive_path="$3"

  download_to "$checksum_url" "$checksum_path"
  verify_checksum "$checksum_path" "$archive_path"
}

prepare_and_verify_binary() {
  local binary_path="$1"
  local extraction_dir="$2"
  local expected_version="${3:-}"

  # macOS: clear quarantine and re-adhoc-sign onedir libs+binary. Stale or
  # post-mutation signatures otherwise SIGKILL --version (exit 137,
  # CODESIGNING / Invalid Page) on consumer Macs.
  clear_macos_quarantine "$extraction_dir"
  resign_macos_onedir_adhoc "$binary_path"

  if [ -n "$expected_version" ]; then
    verify_binary_version "$binary_path" "$expected_version"
  else
    verify_binary_version "$binary_path"
  fi
  # Runs on the staged tree under INSTALL_DIR, which is renamed into place
  # afterwards, so the validation paid here is the validation the user's
  # first launch would otherwise pay.
}

warm_first_launch() {
  local binary_path="$1"

  # Codesign-cache warm-up is Darwin-only; Linux/Windows have no equivalent
  # and would otherwise pay for a full registry/verifier/skills import.
  [ "$(uname -s 2>/dev/null || true)" = "Darwin" ] || return 0

  # macOS validates each Mach-O image on first load and caches the result per
  # file. ``--version`` loads only a few images; the smoke imports the whole
  # tool registry and loads nearly all of them, so the user's first ``opensre``
  # starts warm (~0.2s) instead of paying ~6s of validation. The staged tree
  # is renamed into place afterwards, which keeps the cache.
  run_with_dots "Preparing OpenSRE for first launch" package_smoke_quiet "$binary_path" \
    || printf 'warning: first-launch warm-up did not complete; the first "%s" may start slowly.\n' "$BIN_NAME" >&2
}

package_smoke_quiet() {
  # The smoke prints a JSON summary; only its exit status matters here.
  "$1" _package-smoke >/dev/null 2>&1
}

verify_staged_binary() {
  local staged_path="$1"

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    prepare_and_verify_binary "$staged_path" "${staged_path%/*}"
  else
    prepare_and_verify_binary "$staged_path" "${staged_path%/*}" "$version"
  fi
}

get_binary_path_from_archive() {
  local extraction_root="$1"
  local binary_name="$2"
  local direct_binary_path
  local binary_candidates=()
  local binary_locations

  direct_binary_path="${extraction_root}/${binary_name}"
  if [ -f "$direct_binary_path" ]; then
    printf '%s\n' "$direct_binary_path"
    return
  fi

  need_cmd find

  while IFS= read -r candidate; do
    binary_candidates+=("$candidate")
  done < <(find "$extraction_root" -type f -name "$binary_name")

  case "${#binary_candidates[@]}" in
    1)
      printf '%s\n' "${binary_candidates[0]}"
      ;;
    0)
      die "Archive did not contain '${binary_name}'."
      ;;
    *)
      binary_locations="$(printf '%s, ' "${binary_candidates[@]}")"
      binary_locations="${binary_locations%, }"
      die "Found multiple '${binary_name}' files after extraction: ${binary_locations}"
      ;;
  esac
}

verify_binary_version() {
  local binary_path="$1"
  local expected_version="${2:-}"
  local version_output
  local version_status
  local actual_version

  set +e
  version_output="$("$binary_path" --version 2>&1)"
  version_status=$?
  set -e

  if [ "$version_status" -ne 0 ]; then
    printf 'Failed to execute %s --version (exit %s).\n' "${binary_path##*/}" "$version_status" >&2
    if [ -n "$version_output" ]; then
      printf 'Command output:\n%s\n' "$version_output" >&2
    else
      printf 'Command output: <empty>\n' >&2
    fi
    # 137 = 128+SIGKILL. On Darwin this is often CODESIGNING / Invalid Page for
    # a broken adhoc main-build artifact (not a missing quarantine xattr).
    if [ "$version_status" -eq 137 ] && [ "$(uname -s 2>/dev/null || true)" = "Darwin" ]; then
      printf 'Hint: exit 137 on macOS usually means the kernel killed the binary for an invalid code signature (adhoc main-build). Check Console DiagnosticReports for "Code Signature Invalid", re-download a notarized release, or install from source/uv.\n' >&2
    fi
    print_binary_diagnostics "$binary_path"
    return 1
  fi

  actual_version="$(printf '%s\n' "$version_output" | sed -n 's/.*\([0-9][0-9][0-9][0-9]\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n 1)"

  if [ -z "$expected_version" ]; then
    if [ -n "$actual_version" ]; then
      printf '%s\n' "$actual_version"
    else
      printf 'main\n'
    fi
    return
  fi

  case "$version_output" in
    *"$expected_version"*)
      printf '%s\n' "$expected_version"
      ;;
    *)
      if [ -n "$requested_version" ] || [ -z "$actual_version" ]; then
        die "Downloaded binary version mismatch. Expected '${expected_version}' but got: ${version_output}"
      fi

      warn "Latest release metadata reports v${expected_version}, but the downloaded binary reports v${actual_version}. Installing the verified binary anyway."
      printf '%s\n' "$actual_version"
      ;;
  esac
}

clear_macos_quarantine() {
  local target_path="$1"

  # Only relevant on Darwin; no-op elsewhere / when xattr is missing.
  [ "$(uname -s 2>/dev/null || true)" = "Darwin" ] || return 0
  command -v xattr >/dev/null 2>&1 || return 0
  [ -e "$target_path" ] || return 0
  xattr -dr com.apple.quarantine "$target_path" >/dev/null 2>&1 || true
}

resign_macos_onedir_adhoc() {
  local binary_path="$1"
  local bundle_dir
  local jobs

  # PyInstaller onedir: post-build dylib rewrites (or a stale CI signature) leave
  # Invalid Page codesign faults. Consumer Macs SIGKILL --version (exit 137).
  # Sign nested libs first, then the main binary (same order as release.yml).
  [ "$(uname -s 2>/dev/null || true)" = "Darwin" ] || return 0
  command -v codesign >/dev/null 2>&1 || return 0
  [ -f "$binary_path" ] || return 0
  bundle_dir="$(cd "$(dirname "$binary_path")" && pwd)"
  clear_macos_quarantine "$bundle_dir"
  # Nested libs are independent; parallelize with a small cap so large hosts
  # do not stampede the disk. The main binary stays serial and last.
  jobs="$(sysctl -n hw.ncpu 2>/dev/null || printf '4')"
  if [ "$jobs" -gt 4 ]; then
    jobs=4
  fi
  if [ "$jobs" -lt 1 ]; then
    jobs=1
  fi
  find "$bundle_dir" -type f \( -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0 \
    | xargs -0 -P "$jobs" -n 1 codesign --force --sign - >/dev/null 2>&1 \
    || true
  codesign --force --sign - "$binary_path" >/dev/null 2>&1 || true
}

print_binary_diagnostics() {
  local binary_path="$1"

  printf 'Binary diagnostics:\n' >&2
  printf '  path: %s\n' "$binary_path" >&2
  if command -v uname >/dev/null 2>&1; then
    printf '  system: %s\n' "$(uname -a 2>/dev/null || true)" >&2
  fi
  if command -v ls >/dev/null 2>&1; then
    ls -l "$binary_path" >&2 2>/dev/null || true
  fi
  if command -v file >/dev/null 2>&1; then
    file "$binary_path" >&2 2>/dev/null || true
  fi
  if command -v xattr >/dev/null 2>&1; then
    printf '  xattr: %s\n' "$(xattr -l "$binary_path" 2>/dev/null || printf '(none)')" >&2
  fi
  if [ "$platform" = "linux" ] && command -v ldd >/dev/null 2>&1; then
    ldd "$binary_path" >&2 2>/dev/null || true
  fi
}

configure_path() {
  case ":$PATH:" in
    *":${INSTALL_DIR}:"*)
      return
      ;;
  esac

  if [ "$platform" = "windows" ]; then
    warn "'${INSTALL_DIR}' is not in PATH for this shell. Add it to Git Bash or Windows PATH to run ${BIN_NAME:-opensre} from any terminal."
    return
  fi

  local rc_file=""
  local path_line=""
  local shell_name
  shell_name="${SHELL##*/}"

  case "$shell_name" in
    zsh)
      rc_file="${HOME}/.zshrc"
      path_line="export PATH=\"${INSTALL_DIR}:\$PATH\""
      ;;
    bash)
      if [ "$platform" = "darwin" ]; then
        rc_file="${HOME}/.bash_profile"
      else
        rc_file="${HOME}/.bashrc"
      fi
      path_line="export PATH=\"${INSTALL_DIR}:\$PATH\""
      ;;
    fish)
      rc_file="${HOME}/.config/fish/config.fish"
      path_line="fish_add_path \"${INSTALL_DIR}\""
      ;;
    *)
      log "Add ${INSTALL_DIR} to PATH to run ${BIN_NAME:-opensre} from any terminal."
      return
      ;;
  esac

  local rc_dir="${rc_file%/*}"
  [ "$rc_dir" != "$rc_file" ] && [ ! -d "$rc_dir" ] && mkdir -p "$rc_dir"

  if [ -f "$rc_file" ] && grep -qF "${INSTALL_DIR}" "$rc_file"; then
    muted "PATH already configured in ${rc_file}"
    return
  fi

  local marker="# Added by opensre installer"
  if [ -f "$rc_file" ] && grep -qF "$marker" "$rc_file" && grep -qF "${INSTALL_DIR}" "$rc_file"; then
    muted "PATH already configured in ${rc_file}"
    return
  fi

  printf '\n%s\n%s\n' "$marker" "$path_line" >> "$rc_file"
  muted "PATH configured in ${rc_file}"
}

ensure_on_path() {
  run_with_dots "Checking PATH configuration" ensure_on_path_impl
}

ensure_on_path_impl() {
  if path_has_dir "$INSTALL_DIR"; then
    muted "PATH already configured"
    return
  fi

  configure_path
}

cleanup() {
  if [ -n "${tmp_dir:-}" ] && [ -d "$tmp_dir" ]; then
    rm -rf "$tmp_dir"
  fi
}

detect_platform() {
  local os
  local arch

  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Linux)
      platform="linux"
      ;;
    Darwin)
      platform="darwin"
      ;;
    MINGW*|MSYS*|CYGWIN*)
      platform="windows"
      BIN_NAME="opensre.exe"
      log "Detected Windows environment (${os})."
      ;;
    *)
      die "Unsupported operating system: $os"
      ;;
  esac

  case "$arch" in
    x86_64|amd64)
      target_arch="x64"
      ;;
    arm64|aarch64)
      target_arch="arm64"
      ;;
    *)
      die "Unsupported architecture: $arch"
      ;;
  esac
}

resolve_release_metadata() {
  version="$requested_version"
  release_tag=""

  release_json="$(fetch_release_json "$version")" || {
    if [ "$INSTALL_CHANNEL" = "main" ]; then
      die "Failed to query main build metadata from GitHub."
    fi

    die "Failed to query release metadata from GitHub."
  }

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    release_tag="$(extract_tag_name "$release_json")"
  else
    if [ -z "$version" ]; then
      version="$(extract_tag_name "$release_json")"
    fi
    release_tag="v${version}"
  fi

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    [ -n "$release_tag" ] || die "Failed to determine the main build tag."
  else
    [ -n "$version" ] || die "Failed to determine the release version."
  fi
}

select_archive_asset() {
  local fallback_archive

  asset_arch="$target_arch"
  archive="$(build_archive_name "$version" "$asset_arch")"

  if [ "$platform" = "windows" ] && [ "$target_arch" = "arm64" ] && ! release_has_asset "$release_json" "$archive"; then
    fallback_archive="$(build_archive_name "$version" "x64")"

    if release_has_asset "$release_json" "$fallback_archive"; then
      asset_arch="x64"
      archive="$fallback_archive"
      warn "Windows ARM64 artifact is not published for v${version}; falling back to the x64 build."
    fi
  fi

  if release_has_asset "$release_json" "$archive"; then
    return
  fi

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    die "Main build release does not include asset '${archive}'."
  fi

  die "Release v${version} does not include asset '${archive}'."
}

prepare_download() {
  download_url="https://github.com/${REPO}/releases/download/${release_tag}/${archive}"
  checksum_asset="${archive}.sha256"
  checksum_url="${download_url}.sha256"

  if [ "$asset_arch" != "$target_arch" ]; then
    log "Using release asset built for ${platform}/${asset_arch}."
  fi
  if install_verbose; then
    log "  ${download_url}"
  fi
}

dir_available_kib() {
  local dir="$1"
  local avail

  command -v df >/dev/null 2>&1 || return 1
  avail="$(df -Pk "$dir" 2>/dev/null | awk 'NR==2 { print $4 }')"
  [ -n "$avail" ] || return 1
  printf '%s\n' "$avail"
}

select_temp_base_dir() {
  local min_kib="$1"
  local candidate
  local avail

  for candidate in "${TMPDIR:-}" "${HOME:-}/.cache" /var/tmp /tmp; do
    [ -n "$candidate" ] || continue
    [ -d "$candidate" ] || continue
    is_candidate_dir_writable "$candidate" || continue

    avail="$(dir_available_kib "$candidate")" || continue
    if [ "$avail" -ge "$min_kib" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

create_temp_workspace() {
  local temp_base_dir
  local min_kib=524288

  need_cmd mktemp

  if temp_base_dir="$(select_temp_base_dir "$min_kib")"; then
    tmp_dir="$(mktemp -d "${temp_base_dir%/}/opensre-install.XXXXXX")"
  else
    warn "Could not find a temp directory with at least 512MB free; falling back to the system default and hoping for the best."
    tmp_dir="$(mktemp -d)"
  fi

  trap cleanup EXIT
}

download_release_archive() {
  local download_label

  archive_path="${tmp_dir}/${archive}"
  if [ "$INSTALL_CHANNEL" = "main" ]; then
    download_label="Downloading OpenSRE main build for ${platform}-${asset_arch}"
  else
    download_label="Downloading OpenSRE v${version} for ${platform}-${asset_arch}"
  fi

  run_with_dots "$download_label" download_to "$download_url" "$archive_path" \
    || die "Failed to download '${archive}'."
}

verify_release_checksum() {
  local checksum_path

  if release_has_asset "$release_json" "$checksum_asset"; then
    checksum_path="${tmp_dir}/${checksum_asset}"
    run_with_dots "Fetching and verifying checksum" \
      download_and_verify_checksum "$checksum_url" "$checksum_path" "$archive_path" \
      || die "Failed to download or verify checksum '${checksum_asset}'."
    muted "Checksum verification passed"
    return
  fi

  if [ "$INSTALL_CHANNEL" = "main" ]; then
    warn "Main build release is missing checksum asset '${checksum_asset}'."
  else
    warn "Release v${version} is missing checksum asset '${checksum_asset}'."
  fi
}

extract_release_binary() {
  run_with_dots "Extracting OpenSRE" extract_archive "$archive_path" "$tmp_dir" \
    || die "Failed to extract '${archive}'."
  binary_path="$(
    run_with_dots "Locating ${BIN_NAME} binary" \
      get_binary_path_from_archive "$tmp_dir" "$BIN_NAME"
  )" || die "Failed to locate ${BIN_NAME} in '${archive}'."
}

install_release_binary() {
  local staged_path

  staged_path="$(run_with_dots "Installing OpenSRE" stage_binary "$binary_path")" \
    || die "Failed to install ${BIN_NAME} to '${INSTALL_DIR}'."
  if ! installed_version="$(
    run_with_dots "Found ${BIN_NAME} binary, verifying it runs" verify_staged_binary "$staged_path"
  )"; then
    discard_staged_binary "$staged_path"
    die "Failed to verify '${archive}'."
  fi
  warm_first_launch "$staged_path"
  activate_staged_binary "$staged_path" "${INSTALL_DIR}/${BIN_NAME}" \
    || die "Failed to install ${BIN_NAME} to '${INSTALL_DIR}'."
}

print_install_confirmation() {
  if [ "$installed_version" = "main" ]; then
    muted "OpenSRE main build installed successfully to ${INSTALL_DIR}/${BIN_NAME}"
  else
    muted "OpenSRE v${installed_version} installed successfully to ${INSTALL_DIR}/${BIN_NAME}"
  fi
}

auto_setup_enabled() {
  case "${OPENSRE_AUTO_LAUNCH:-}" in
    0|false|FALSE|no|NO|off|OFF)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

launch_setup_after_install() {
  local binary_path="${INSTALL_DIR}/${BIN_NAME}"

  if ! auto_setup_enabled || [ ! -t 0 ] || [ ! -t 1 ]; then
    return 0
  fi
  log "Launching ${BIN_NAME} setup..."
  if ! "$binary_path" setup; then
    warn "Setup exited before completion. Run '${BIN_NAME} setup' to retry."
  fi
}

finish_install() {
  print_install_confirmation
  ensure_on_path
  ensure_github_cli
  log "${COLOR_YELLOW:-}Run '${BIN_NAME}' to get started!${COLOR_RESET:-}"
  launch_setup_after_install
}

main() {
  parse_args "$@"
  require_prerequisites
  detect_platform
  resolve_install_dir
  resolve_release_metadata
  select_archive_asset
  prepare_download
  create_temp_workspace
  download_release_archive
  verify_release_checksum
  extract_release_binary
  install_release_binary
  finish_install
}

main "$@"
