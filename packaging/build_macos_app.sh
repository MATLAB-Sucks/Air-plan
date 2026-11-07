#!/usr/bin/env bash
# Build "Aircraft Foundry.app" for the architecture of this Mac.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
python -m pip install --only-binary :all: "pyinstaller==6.16.0"
rm -rf build dist
pyinstaller --noconfirm packaging/aircraft_foundry.spec
APP="dist/Aircraft Foundry.app"
echo "Built: $APP  ($(du -sh "$APP" | cut -f1))"
echo "Self-test:"
"$APP/Contents/MacOS/AircraftFoundry" --self-test
echo
echo "The app is unsigned. To distribute it, sign and notarise:"
echo "  codesign --deep --force --options runtime --sign 'Developer ID Application: …' \"$APP\""
echo "  xcrun notarytool submit … && xcrun stapler staple \"$APP\""
echo "For local use: right-click the app > Open the first time (Gatekeeper)."
