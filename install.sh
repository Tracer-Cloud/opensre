#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || {
  printf '%s\n' "Error: install.sh requires bash. Run 'bash install.sh' or pipe it into bash." >&2
  exit 1
}

set -euo pipefail

REPO="${OPENSRE_INSTALL_REPO:-Tracer-Cloud/opensre}"
DEFAULT_INSTALL_DIR="${HOME}/.local/bin"
USER_INSTALL_DIR_CANDIDATES="${OPENSRE_USER_INSTALL_DIR_CANDIDATES:-$HOME/.local/bin:$HOME/bin}"
SYSTEM_INSTALL_DIR_CANDIDATES="${OPENSRE_SYSTEM_INSTALL_DIR_CANDIDATES:-/opt/homebrew/bin:/usr/local/bin:/opt/local/bin}"
INSTALL_DIR="${OPENSRE_INSTALL_DIR:-}"
INSTALL_DIR_OVERRIDE=0
INSTALL_CHANNEL="${OPENSRE_INSTALL_CHANNEL:-release}"
BIN_NAME="opensre"
INSTALL_WITH_SUDO=0
requested_version="${OPENSRE_VERSION:-}"

[ -n "$INSTALL_DIR" ] && INSTALL_DIR_OVERRIDE=1
requested_version="${requested_version#v}"

log() {
  printf '%s\n' "$*"
}

warn() {
  printf 'Warning: %s\n' "$*" >&2
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: install.sh [--main] [--version <version>] [--install-dir <path>]

Installs the OpenSRE CLI.

Options:
  --main                Install the rolling build published from the main branch.
  --version <version>   Install a specific release version (for example 2026.4.29).
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
        ;;
      --release)
        INSTALL_CHANNEL="release"
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

  if [ "$INSTALL_CHANNEL" = "main" ] && [ -n "$requested_version" ]; then
    die "--version cannot be combined with --main."
  fi
}

parse_args "$@"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but was not found in PATH."
}

need_cmd curl
need_cmd grep
need_cmd sed
need_cmd tr
need_cmd uname

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
    api_url="https://api.github.com/repos/${REPO}/releases/tags/main"
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

select_writable_path_candidate_from_list() {
  local candidate_list="$1"
  local old_ifs="$IFS"
  local dir

  IFS=':'
  for dir in $candidate_list; do
    [ -n "$dir" ] || continue
    if path_has_dir "$dir" && is_candidate_dir_writable "$dir"; then
      printf '%s\n' "$dir"
      IFS="$old_ifs"
      return 0
    fi
  done
  IFS="$old_ifs"

  return 1
}

select_path_candidate_for_sudo() {
  local candidate_list="$1"
  local old_ifs="$IFS"
  local dir

  command -v sudo >/dev/null 2>&1 || return 1
  [ "${EUID:-0}" -ne 0 ] || return 1
  [ "$INSTALL_DIR_OVERRIDE" -eq 0 ] || return 1

  IFS=':'
  for dir in $candidate_list; do
    [ -n "$dir" ] || continue
    if path_has_dir "$dir"; then
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

    if [ -n "$existing_dir" ] && path_has_dir "$existing_dir" && is_candidate_dir_writable "$existing_dir"; then
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

  if [ -n "$existing_dir" ] && path_has_dir "$existing_dir" && command -v sudo >/dev/null 2>&1 && [ "${EUID:-0}" -ne 0 ] && [ "$INSTALL_DIR_OVERRIDE" -eq 0 ]; then
    INSTALL_DIR="$existing_dir"
    INSTALL_WITH_SUDO=1
    return
  fi

  if INSTALL_DIR="$(select_path_candidate_for_sudo "$SYSTEM_INSTALL_DIR_CANDIDATES")"; then
    INSTALL_WITH_SUDO=1
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

run_with_privilege() {
  if [ "$INSTALL_WITH_SUDO" -eq 1 ]; then
    sudo "$@"
    return
  fi

  "$@"
}

install_binary() {
  local source_path="$1"
  local destination_path="$2"

  if command -v install >/dev/null 2>&1; then
    run_with_privilege install -m 0755 "$source_path" "$destination_path"
    return
  fi

  run_with_privilege cp "$source_path" "$destination_path"
  run_with_privilege chmod 0755 "$destination_path" 2>/dev/null || true
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
      die "Archive '${archive}' did not contain '${binary_name}'."
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
  local actual_version

  if ! version_output="$("$binary_path" --version 2>&1)"; then
    die "Failed to execute '${binary_path##*/} --version': ${version_output}"
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
      path_line="export PATH=\"\$PATH:${INSTALL_DIR}\""
      ;;
    bash)
      if [ "$platform" = "darwin" ]; then
        rc_file="${HOME}/.bash_profile"
      else
        rc_file="${HOME}/.bashrc"
      fi
      path_line="export PATH=\"\$PATH:${INSTALL_DIR}\""
      ;;
    fish)
      rc_file="${HOME}/.config/fish/config.fish"
      path_line="fish_add_path \"${INSTALL_DIR}\""
      ;;
    *)
      log "Add the following line to your shell profile to use ${BIN_NAME:-opensre}:"
      log "  export PATH=\"\$PATH:${INSTALL_DIR}\""
      return
      ;;
  esac

  local rc_dir="${rc_file%/*}"
  [ "$rc_dir" != "$rc_file" ] && [ ! -d "$rc_dir" ] && mkdir -p "$rc_dir"

  if [ -f "$rc_file" ] && grep -qF "${INSTALL_DIR}" "$rc_file"; then
    return
  fi

  local marker="# Added by opensre installer"
  if [ -f "$rc_file" ] && grep -qF "$marker" "$rc_file" && grep -qF "${INSTALL_DIR}" "$rc_file"; then
    return
  fi

  printf '\n%s\n%s\n' "$marker" "$path_line" >> "$rc_file"

  log ""
  log "${BIN_NAME:-opensre} has been added to PATH in ${rc_file}."
  log "To apply now, run:  source \"${rc_file}\""
  log "Or open a new terminal."
}

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

resolve_install_dir

version="$requested_version"
release_tag=""

if [ "$INSTALL_CHANNEL" = "main" ]; then
  log "Fetching latest main build metadata..."
elif [ -z "$version" ]; then
  log "Fetching latest release version..."
fi

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

if ! release_has_asset "$release_json" "$archive"; then
  if [ "$INSTALL_CHANNEL" = "main" ]; then
    die "Main build release does not include asset '${archive}'."
  fi

  die "Release v${version} does not include asset '${archive}'."
fi

download_url="https://github.com/${REPO}/releases/download/${release_tag}/${archive}"
checksum_asset="${archive}.sha256"
checksum_url="${download_url}.sha256"

if [ "$INSTALL_CHANNEL" = "main" ]; then
  log "Installing opensre main build (${platform}/${target_arch})..."
else
  log "Installing opensre v${version} (${platform}/${target_arch})..."
fi
if [ "$asset_arch" != "$target_arch" ]; then
  log "Using release asset built for ${platform}/${asset_arch}."
fi
log "Downloading ${download_url}"

need_cmd mktemp
tmp_dir="$(mktemp -d)"

cleanup() {
  if [ -n "${tmp_dir:-}" ] && [ -d "$tmp_dir" ]; then
    rm -rf "$tmp_dir"
  fi
}

trap cleanup EXIT

archive_path="${tmp_dir}/${archive}"
download_to "$download_url" "$archive_path" || die "Failed to download '${archive}'."

if release_has_asset "$release_json" "$checksum_asset"; then
  checksum_path="${tmp_dir}/${checksum_asset}"
  download_to "$checksum_url" "$checksum_path" || die "Failed to download checksum '${checksum_asset}'."
  verify_checksum "$checksum_path" "$archive_path"
else
  if [ "$INSTALL_CHANNEL" = "main" ]; then
    warn "Main build release is missing checksum asset '${checksum_asset}'."
  else
    warn "Release v${version} is missing checksum asset '${checksum_asset}'."
  fi
fi

if [ "$INSTALL_WITH_SUDO" -eq 1 ]; then
  log "Installing into ${INSTALL_DIR} with sudo so '${BIN_NAME}' is available immediately in this shell."
fi

run_with_privilege mkdir -p "$INSTALL_DIR"
extract_archive "$archive_path" "$tmp_dir"

binary_path="$(get_binary_path_from_archive "$tmp_dir" "$BIN_NAME")"
if [ "$INSTALL_CHANNEL" = "main" ]; then
  installed_version="$(verify_binary_version "$binary_path")"
else
  installed_version="$(verify_binary_version "$binary_path" "$version")"
fi
install_binary "$binary_path" "${INSTALL_DIR}/${BIN_NAME}"

if [ "$INSTALL_CHANNEL" = "main" ]; then
  if [ "$installed_version" = "main" ]; then
    log "Installed ${BIN_NAME} main build to ${INSTALL_DIR}/${BIN_NAME}"
  else
    log "Installed ${BIN_NAME} main build (${installed_version}) to ${INSTALL_DIR}/${BIN_NAME}"
  fi
else
  log "Installed ${BIN_NAME} v${installed_version} to ${INSTALL_DIR}/${BIN_NAME}"
fi

configure_path
