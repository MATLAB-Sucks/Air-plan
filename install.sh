#!/usr/bin/env bash
# Aircraft Foundry - one-command installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/MATLAB-Sucks/Air-plan/main/install.sh | bash
#
# Asks where to install (this computer, a USB / external drive, or any folder) and how:
#   * Ready-to-run app (macOS, recommended) - Python is built in, nothing else is needed.
#   * From source (macOS and Linux) - uses Python 3.11 if this computer has it; otherwise (or on a
#     drive) it puts a self-contained Python inside the install folder, so it works first time and
#     travels with the drive.
# Packaged downloads are checked against the release's published SHA-256.  Run it again to update.
# Saved planes, projects and settings are never touched (user data folder, or AircraftFoundryData on
# a drive).
#
# Unattended:  AF_TARGET=computer|drive|folder  AF_INSTALL_DIR=/Volumes/USB  AF_MODE=app|source
#              AF_VERSION=v0.9.4  AF_NO_LAUNCH=1  AF_YES=1 (accept defaults)
set -euo pipefail

REPO="${AF_REPO:-MATLAB-Sucks/Air-plan}"
VERSION="${AF_VERSION:-latest}"
OS="$(uname -s)"
say() { printf '  %s\n' "$*"; }
die() { printf '\n  Install failed: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "this installer needs '$1'"; }
ask() {  # ask <question> <default>   (reads the terminal: the script itself arrives on stdin)
  local a=""
  if [ -n "${AF_YES:-}" ] || [ -n "${CI:-}" ] || ! { : >/dev/tty; } 2>/dev/null; then printf '%s' "$2"; return; fi
  printf '  %s [%s] ' "$1" "$2" >/dev/tty
  read -r a </dev/tty || true
  printf '%s' "${a:-$2}"
}
need curl
AUTH=()
[ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")   # optional: avoids rate limits
release_json() {  # release_json <repo> <tag|latest>
  local api="https://api.github.com/repos/$1/releases/latest"
  [ "$2" = latest ] || api="https://api.github.com/repos/$1/releases/tags/$2"
  # ${AUTH[@]+...}: macOS's bash 3.2 treats an empty array as unbound under `set -u`
  curl -fsSL ${AUTH[@]+"${AUTH[@]}"} -H 'User-Agent: aircraft-foundry-installer' "$api" || die "couldn't read $1 ($2) from GitHub"
}
jget() { python3 -c "import json,sys,re; d=json.load(sys.stdin); print($1)"; }  # python3 ships with macOS/Linux

INSTALLER_VERSION="2026-11-10.3"   # shown first, so an old cached copy of this script is easy to spot
printf '\n  Aircraft Foundry installer (version %s)\n' "$INSTALLER_VERSION"
# ---------------------------------------------------------------- where
TARGET="${AF_TARGET:-}"
if [ -z "$TARGET" ]; then
  printf '\n'; say "Where should it go?"
  if [ "$OS" = Darwin ]; then say "  1) This computer   (Applications)"; else say "  1) This computer   (~/.local/share/aircraft-foundry)"; fi
  say "  2) A USB stick or external drive   (portable: planes are saved on that drive)"
  say "  3) A folder I choose"
  case "$(ask 'Choose 1, 2 or 3' 1)" in 1) TARGET=computer ;; 2) TARGET=drive ;; 3) TARGET=folder ;; *) die "please answer 1, 2 or 3" ;; esac
fi
DIR="${AF_INSTALL_DIR:-}"
if [ "$TARGET" = drive ] && [ -z "$DIR" ]; then
  if [ "$OS" = Darwin ]; then CANDS=$(ls -d /Volumes/* 2>/dev/null | grep -v '^/Volumes/Macintosh HD' || true)
  else CANDS=$(ls -d /media/"$USER"/* /run/media/"$USER"/* /mnt/* 2>/dev/null || true); fi
  [ -n "$CANDS" ] && { say "Drives found:"; printf '%s\n' "$CANDS" | sed 's/^/      /'; }
  DIR="$(ask 'Drive (path)' "$(printf '%s\n' "$CANDS" | head -1)")"
  [ -n "$DIR" ] || die "no drive given"
elif [ "$TARGET" = folder ] && [ -z "$DIR" ]; then
  DIR="$(ask 'Folder' "$HOME/AircraftFoundry")"
fi
PORTABLE=""; [ "$TARGET" = computer ] || PORTABLE=1
# ---------------------------------------------------------------- how
MODE="${AF_MODE:-}"
if [ "$OS" != Darwin ]; then MODE=source   # no packaged Linux build
elif [ -z "$MODE" ]; then
  printf '\n'; say "How?"
  say "  a) Ready-to-run app (recommended - Python is built in)"
  say "  b) From source (uses Python 3.11; installs its own copy if needed)"
  case "$(ask 'Choose a or b' a)" in a) MODE=app ;; b) MODE=source ;; *) die "please answer a or b" ;; esac
fi
REL="$(release_json "$REPO" "$VERSION")"
TAG="$(printf '%s' "$REL" | jget "d['tag_name']")"
asset_url() { printf '%s' "$REL" | jget "next((a['browser_download_url'] for a in d['assets'] if a['name']=='$1'), '')"; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
verify() {  # verify <file> <asset name>
  local want got
  curl -fsSL -o "$TMP/SHA256SUMS.txt" "$(asset_url SHA256SUMS.txt)" || die "release $TAG has no SHA256SUMS.txt"
  want="$(grep " \*\?$2\$" "$TMP/SHA256SUMS.txt" | awk '{print $1}' | head -1)"
  [ -n "$want" ] || die "SHA256SUMS.txt has no entry for $2"
  if command -v shasum >/dev/null; then got="$(shasum -a 256 "$1" | awk '{print $1}')"; else got="$(sha256sum "$1" | awk '{print $1}')"; fi
  [ "$want" = "$got" ] || die "$2 didn't match its published checksum - not installed"
  say "checksum verified"
}
replace() {  # replace <new> <dest>: keep the old version as .previous until the new one is in place
  rm -rf "$2.previous"; [ -e "$2" ] && mv "$2" "$2.previous"
  if ! mv "$1" "$2"; then rm -rf "$2"; [ -e "$2.previous" ] && mv "$2.previous" "$2"; die "couldn't move the new version into $2"; fi
  rm -rf "$2.previous"
}
unpack() { if command -v ditto >/dev/null; then ditto -x -k "$1" "$2"; else need unzip; unzip -q "$1" -d "$2"; fi; }
LAUNCH=""
printf '\n'; say "release $TAG -> ${DIR:-this computer} ($TARGET, $MODE)"

if [ "$MODE" = app ] && [ -z "$PORTABLE" ]; then
  # ---------------------------------------------------- macOS app on this computer
  ASSET="AircraftFoundry-macOS.zip"; URL="$(asset_url "$ASSET")"; [ -n "$URL" ] || die "release $TAG has no $ASSET"
  if [ -w /Applications ]; then DEST=/Applications; else DEST="$HOME/Applications"; fi
  mkdir -p "$DEST"; say "downloading..."; curl -fL --progress-bar -o "$TMP/$ASSET" "$URL"
  verify "$TMP/$ASSET" "$ASSET"; unpack "$TMP/$ASSET" "$TMP/new"
  SRC="$(find "$TMP/new" -maxdepth 2 -name '*.app' -type d | head -1)"; [ -n "$SRC" ] || die "the archive doesn't contain the app"
  APP="$DEST/Aircraft Foundry.app"
  pgrep -f "$APP/Contents/MacOS/" >/dev/null 2>&1 && die "Aircraft Foundry is running - quit it and run the installer again"
  replace "$SRC" "$APP"; xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
  LAUNCH="$APP"; say "installed to $APP"
elif [ "$MODE" = app ]; then
  # ---------------------------------------------------- portable layout on a drive / folder (Windows + macOS apps)
  ASSET="AircraftFoundry-Portable.zip"; URL="$(asset_url "$ASSET")"; [ -n "$URL" ] || die "release $TAG has no $ASSET"
  mkdir -p "$DIR"; say "downloading (Windows + macOS versions, for use on any computer)..."
  curl -fL --progress-bar -o "$TMP/$ASSET" "$URL"; verify "$TMP/$ASSET" "$ASSET"; unpack "$TMP/$ASSET" "$TMP/usb"
  ROOT="$(dirname "$(find "$TMP/usb" -maxdepth 3 -type d -name AircraftFoundryData | head -1)")"
  [ -d "$ROOT" ] || die "the portable archive has no AircraftFoundryData folder"
  for item in "$ROOT"/*; do
    name="$(basename "$item")"
    if [ "$name" = AircraftFoundryData ]; then   # your data: only created, never overwritten
      [ -e "$DIR/$name" ] || cp -R "$item" "$DIR/$name"
      [ -e "$DIR/$name/portable.json" ] || cp "$item/portable.json" "$DIR/$name/"
    elif [ -d "$item" ]; then replace "$item" "$DIR/$name"
    else cp -f "$item" "$DIR/$name"; fi
  done
  xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
  LAUNCH="$(find "$DIR/macOS" -maxdepth 1 -name '*.app' 2>/dev/null | head -1)"; say "installed to $DIR"
else
  # ---------------------------------------------------- from source, with Python 3.11
  if [ -z "$DIR" ]; then
    if [ "$OS" = Darwin ]; then DIR="$HOME/Applications/AircraftFoundry"; else DIR="$HOME/.local/share/aircraft-foundry"; fi
  fi
  mkdir -p "$DIR"; APPDIR="$DIR/AircraftFoundry-source"; NEW="$TMP/app"
  say "downloading the source of $TAG..."
  curl -fsSL -o "$TMP/src.tar.gz" "https://github.com/$REPO/archive/refs/tags/$TAG.tar.gz" || die "couldn't download the source"
  mkdir -p "$NEW" && tar -xzf "$TMP/src.tar.gz" -C "$NEW" --strip-components=1
  PY=""
  if [ -z "$PORTABLE" ]; then   # reuse this computer's Python 3.11 if it has one
    for c in python3.11 python3; do
      if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))' 2>/dev/null; then PY="$c"; break; fi
    done
  fi
  if [ -n "$PY" ]; then
    say "using this computer's Python 3.11 ($(command -v "$PY"))"
    "$PY" -m venv "$NEW/.venv"; PYEXE="$NEW/.venv/bin/python"; RELPY="AircraftFoundry-source/.venv/bin/python"
  else
    say "Python 3.11 not found${PORTABLE:+ (or installing to a drive)}: installing a self-contained Python into the install folder"
    case "$OS/$(uname -m)" in
      Darwin/arm64) TRIPLE=aarch64-apple-darwin ;; Darwin/*) TRIPLE=x86_64-apple-darwin ;;
      Linux/aarch64) TRIPLE=aarch64-unknown-linux-gnu ;; *) TRIPLE=x86_64-unknown-linux-gnu ;;
    esac
    PURL="$(release_json astral-sh/python-build-standalone latest | jget "next((a['browser_download_url'] for a in d['assets'] if re.match(r'cpython-3\.11\.\d+\+\d+-$TRIPLE-install_only\.tar\.gz$', a['name'])), '')")"
    [ -n "$PURL" ] || die "couldn't find a Python 3.11 build for $TRIPLE"
    curl -fsSL -o "$TMP/python.tar.gz" "$PURL"; tar -xzf "$TMP/python.tar.gz" -C "$NEW"   # -> $NEW/python
    PYEXE="$NEW/python/bin/python3"; RELPY="AircraftFoundry-source/python/bin/python3"
  fi
  case "$OS/$(uname -m)" in
    Darwin/arm64) LOCK=lock-macos-arm64-py311.txt ;; Darwin/*) LOCK=lock-macos-intel-py311.txt ;; *) LOCK=lock-linux-x86_64-py311.txt ;;
  esac
  if [ -z "${AF_SKIP_PACKAGES:-}" ]; then
    say "installing the Python packages (a few minutes)..."
    "$PYEXE" -m pip install -q --upgrade pip
    "$PYEXE" -m pip install -q --only-binary :all: -r "$NEW/requirements/$LOCK" || die "installing the packages failed"
    "$PYEXE" -m pip install -q --no-deps "$NEW" || die "installing Aircraft Foundry failed"
  fi
  [ -d "$NEW/.venv" ] && sed -i.bak "s#$NEW#$APPDIR#g" "$NEW/.venv/pyvenv.cfg" "$NEW/.venv/bin/activate" 2>/dev/null || true
  replace "$NEW" "$APPDIR"
  # launcher with a relative path, so it works wherever the drive is mounted
  LAUNCH_SH="$DIR/aircraft-foundry"
  printf '#!/usr/bin/env bash\nHERE="$(cd "$(dirname "$0")" && pwd)"\n# portable: keep planes on this drive, wherever it is mounted\n[ -f "$HERE/AircraftFoundryData/portable.json" ] && export AIRCRAFT_FOUNDRY_DATA="$HERE/AircraftFoundryData"\nexec "$HERE/%s" -m aircraft_foundry.app.main "$@"\n' "$RELPY" > "$LAUNCH_SH"
  chmod +x "$LAUNCH_SH"
  if [ "$OS" = Darwin ]; then cp "$LAUNCH_SH" "$DIR/Aircraft Foundry.command"; chmod +x "$DIR/Aircraft Foundry.command"; fi
  if [ -n "$PORTABLE" ] && [ ! -e "$DIR/AircraftFoundryData/portable.json" ]; then   # keep data on the drive
    mkdir -p "$DIR/AircraftFoundryData"; printf '{"portable": true}\n' > "$DIR/AircraftFoundryData/portable.json"
  fi
  if [ "$OS" = Linux ] && [ -z "$PORTABLE" ]; then
    mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
    ln -sf "$LAUNCH_SH" "$HOME/.local/bin/aircraft-foundry"
    printf '[Desktop Entry]\nType=Application\nName=Aircraft Foundry\nExec=%s\nTerminal=false\nCategories=Education;Science;\n' \
      "$LAUNCH_SH" > "$HOME/.local/share/applications/aircraft-foundry.desktop"
  fi
  LAUNCH="$LAUNCH_SH"; say "installed to $DIR"
fi
printf '\n  Done. Your saved planes and settings are kept between updates.\n'
if [ -n "$PORTABLE" ]; then
  if [ "$MODE" = app ]; then say "On another computer, open the drive and start the app from its Windows or macOS folder."
  else say "On another computer (same kind of system), run $DIR/aircraft-foundry."; fi
fi
if [ -z "${AF_NO_LAUNCH:-}" ] && [ -n "$LAUNCH" ]; then
  if [ "$OS" = Darwin ] && [ -d "$LAUNCH" ]; then open "$LAUNCH"; else (nohup "$LAUNCH" >/dev/null 2>&1 &); fi
fi
