#!/usr/bin/env bash
# Aircraft Foundry - one-command installer for macOS (and Linux, from source).
#
#   curl -fsSL https://raw.githubusercontent.com/MATLAB-Sucks/Air-plan/main/install.sh | bash
#
# macOS: downloads the latest release, checks its SHA-256 against SHA256SUMS.txt and installs
#        "Aircraft Foundry.app" into /Applications (or ~/Applications if that isn't writable).
# Linux: there is no packaged Linux build, so it installs from source into its own Python 3.11
#        environment (~/.local/share/aircraft-foundry/app) with an `aircraft-foundry` command and a
#        desktop entry. Python 3.11 is needed - or `uv`, which can fetch it.
# Run it again to update. Saved planes, projects and settings are kept (separate data folder).
#
# Options (environment variables):  AF_VERSION=v0.9.3  AF_INSTALL_DIR=...  AF_NO_LAUNCH=1  AF_REPO=owner/repo
set -euo pipefail

REPO="${AF_REPO:-MATLAB-Sucks/Air-plan}"
VERSION="${AF_VERSION:-latest}"
say() { printf '  %s\n' "$*"; }
die() { printf '\n  Install failed: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "this installer needs '$1'"; }
need curl

printf '\n  Aircraft Foundry installer\n'
if [ "$VERSION" = latest ]; then API="https://api.github.com/repos/$REPO/releases/latest"
else API="https://api.github.com/repos/$REPO/releases/tags/$VERSION"; fi
AUTH=()
[ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")   # optional: avoids rate limits
REL_JSON="$(curl -fsSL "${AUTH[@]}" -H 'User-Agent: aircraft-foundry-installer' "$API")" \
  || die "couldn't read the release from GitHub. Is github.com/$REPO public and released?"
json_get() {  # json_get <python expression on d>   (python3 ships with macOS and Linux)
  printf '%s' "$REL_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"
}
TAG="$(json_get "d['tag_name']")"
asset_url() { json_get "next((a['browser_download_url'] for a in d['assets'] if a['name']=='$1'), '')"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

verify() {  # verify <file> <asset name>
  local want got
  curl -fsSL -o "$TMP/SHA256SUMS.txt" "$(asset_url SHA256SUMS.txt)" || die "release $TAG has no SHA256SUMS.txt"
  want="$(grep " \*\?$2\$" "$TMP/SHA256SUMS.txt" | awk '{print $1}' | head -1)"
  [ -n "$want" ] || die "SHA256SUMS.txt has no entry for $2"
  if command -v shasum >/dev/null; then got="$(shasum -a 256 "$1" | awk '{print $1}')"
  else got="$(sha256sum "$1" | awk '{print $1}')"; fi
  [ "$want" = "$got" ] || die "the download didn't match its published checksum - not installed"
  say "checksum verified"
}

case "$(uname -s)" in
Darwin)
  ASSET="AircraftFoundry-macOS.zip"
  URL="$(asset_url "$ASSET")"; [ -n "$URL" ] || die "release $TAG has no $ASSET"
  if [ -n "${AF_INSTALL_DIR:-}" ]; then DEST="$AF_INSTALL_DIR"
  elif [ -w /Applications ]; then DEST="/Applications"
  else DEST="$HOME/Applications"; fi
  mkdir -p "$DEST"
  say "release $TAG -> $DEST"
  say "downloading..."
  curl -fL --progress-bar -o "$TMP/$ASSET" "$URL"
  verify "$TMP/$ASSET" "$ASSET"
  ditto -x -k "$TMP/$ASSET" "$TMP/new"
  APP_SRC="$(find "$TMP/new" -maxdepth 2 -name '*.app' -type d | head -1)"
  [ -n "$APP_SRC" ] || die "the archive doesn't contain the app"
  APP="$DEST/Aircraft Foundry.app"
  if pgrep -f "$APP/Contents/MacOS/" >/dev/null 2>&1; then die "Aircraft Foundry is running - quit it and run the installer again"; fi
  rm -rf "$APP.previous"
  [ -e "$APP" ] && mv "$APP" "$APP.previous"
  if ! ditto "$APP_SRC" "$APP"; then
    rm -rf "$APP"; [ -e "$APP.previous" ] && mv "$APP.previous" "$APP"
    die "couldn't copy the app into $DEST"
  fi
  xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
  rm -rf "$APP.previous"
  say "installed $TAG to $APP"
  printf '\n  Done. Your saved planes and settings are kept between updates.\n'
  [ -n "${AF_NO_LAUNCH:-}" ] || open "$APP"
  ;;
Linux)
  DEST="${AF_INSTALL_DIR:-$HOME/.local/share/aircraft-foundry/app}"
  need tar
  say "release $TAG (from source) -> $DEST"
  curl -fsSL -o "$TMP/src.tar.gz" "https://github.com/$REPO/archive/refs/tags/$TAG.tar.gz" \
    || die "couldn't download the source of $TAG"
  mkdir -p "$TMP/src" && tar -xzf "$TMP/src.tar.gz" -C "$TMP/src" --strip-components=1
  PY=""
  for c in python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'; then PY="$c"; break; fi
  done
  rm -rf "$DEST.new" && mkdir -p "$(dirname "$DEST")"
  cp -R "$TMP/src" "$DEST.new"
  if [ -n "$PY" ]; then "$PY" -m venv "$DEST.new/.venv"
  elif command -v uv >/dev/null; then uv venv -q --python 3.11 "$DEST.new/.venv"
  else die "Python 3.11 is needed (or install uv: https://docs.astral.sh/uv/ - it can fetch Python 3.11)"; fi
  VPY="$DEST.new/.venv/bin/python"
  say "installing dependencies (a few minutes the first time)..."
  if command -v uv >/dev/null; then
    uv pip install -q --python "$VPY" -r "$DEST.new/requirements/lock-linux-x86_64-py311.txt"
    uv pip install -q --python "$VPY" --no-deps -e "$DEST.new"
  else
    "$VPY" -m pip install -q --upgrade pip
    "$VPY" -m pip install -q --only-binary :all: -r "$DEST.new/requirements/lock-linux-x86_64-py311.txt"
    "$VPY" -m pip install -q --no-deps -e "$DEST.new"
  fi
  rm -rf "$DEST.previous"; [ -e "$DEST" ] && mv "$DEST" "$DEST.previous"
  mv "$DEST.new" "$DEST"
  # the editable install points at the old path: re-point it at the final location
  if command -v uv >/dev/null; then uv pip install -q --python "$DEST/.venv/bin/python" --no-deps -e "$DEST" 2>/dev/null || true
  else "$DEST/.venv/bin/python" -m pip install -q --no-deps -e "$DEST" 2>/dev/null || true; fi
  rm -rf "$DEST.previous"
  mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
  cat > "$HOME/.local/bin/aircraft-foundry" <<LAUNCH
#!/usr/bin/env bash
exec "$DEST/.venv/bin/python" -m aircraft_foundry.app.main "\$@"
LAUNCH
  chmod +x "$HOME/.local/bin/aircraft-foundry"
  cat > "$HOME/.local/share/applications/aircraft-foundry.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Aircraft Foundry
Comment=Conceptual aircraft design
Exec=$HOME/.local/bin/aircraft-foundry
Terminal=false
Categories=Education;Science;Engineering;
DESK
  say "installed $TAG to $DEST - run 'aircraft-foundry' (make sure ~/.local/bin is on your PATH)"
  printf '\n  Done. Your saved planes and settings are kept between updates.\n'
  [ -n "${AF_NO_LAUNCH:-}" ] || (nohup "$HOME/.local/bin/aircraft-foundry" >/dev/null 2>&1 &)
  ;;
*) die "unsupported system '$(uname -s)'. On Windows use: irm https://raw.githubusercontent.com/$REPO/main/install.ps1 | iex" ;;
esac
