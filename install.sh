#!/usr/bin/env bash

set -euo pipefail

REPO="${OPENSRE_INSTALL_REPO:-Tracer-Cloud/opensre}"
INSTALL_DIR="${OPENSRE_INSTALL_DIR:-$HOME/.local/bin}"
BIN_NAME="opensre"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "$1 is required to install ${BIN_NAME}." >&2
    exit 1
  }
}

need_cmd curl
need_cmd tar
need_cmd uname

os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
  Darwin) platform="darwin" ;;
  Linux) platform="linux" ;;
  *)
    echo "Unsupported operating system: $os" >&2
    exit 1
    ;;
esac

case "$arch" in
  x86_64|amd64) target_arch="x64" ;;
  arm64|aarch64) target_arch="arm64" ;;
  *)
    echo "Unsupported architecture: $arch" >&2
    exit 1
    ;;
esac

target="${platform}-${target_arch}"

version="${OPENSRE_VERSION:-}"
if [ -z "$version" ]; then
  version="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" | sed -n 's/.*"tag_name":[[:space:]]*"v\{0,1\}\([^"]*\)".*/\1/p' | head -n 1)"
fi

if [ -z "$version" ]; then
  echo "Failed to determine the latest release version." >&2
  exit 1
fi

archive="opensre_${version}_${target}.tar.gz"
download_url="https://github.com/${REPO}/releases/download/v${version}/${archive}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

echo "Downloading ${download_url}"
curl -fsSL "$download_url" -o "${tmp_dir}/${archive}"

mkdir -p "$INSTALL_DIR"
tar -xzf "${tmp_dir}/${archive}" -C "$tmp_dir"
install -m 0755 "${tmp_dir}/${BIN_NAME}" "${INSTALL_DIR}/${BIN_NAME}"

echo "Installed ${BIN_NAME} ${version} to ${INSTALL_DIR}/${BIN_NAME}"
case ":$PATH:" in
  *":${INSTALL_DIR}:"*) ;;
  *)
    echo "Add ${INSTALL_DIR} to your PATH to run ${BIN_NAME} from any shell." >&2
    ;;
esac
