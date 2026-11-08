# Updates, compatibility and game controllers

## Updating

Press **⟳ Check for updates** in the status bar, or use *Help → Check for
updates…*. The app also checks quietly once a day at start-up, and the button
changes to **⬆ Update available** when a new version is out.

**How an update works**

1. The app reads the latest **GitHub Release** of the repository (default
   `MATLAB-Sucks/Air-plan`; you can change it in the update window).
2. It downloads the build for your computer and checks its **SHA-256** against
   the release's `SHA256SUMS.txt`. Anything that doesn't match is discarded.
3. It copies your design library to `backups/` in the data folder.
4. It unpacks the new version next to the program. The app then closes, a small
   script swaps the old and new versions (keeping the old one as
   `….previous`), and the new version starts. If the swap fails, the old version
   is put back.
5. A copy run from a git checkout updates with `git pull --ff-only` instead.

**Your saved planes are never touched.** They live in the data folder
(`AircraftFoundryData` on a USB stick, otherwise your user data folder), which
is separate from the program folder. The updater refuses to run if the data
folder is somehow inside the program folder.

**Public or private repository?** Checking a *public* repository needs no
account or token. For a private one, paste a read-only token into the update
window; it is stored only on that computer.

**Publishing a release (for maintainers)**

1. Bump the version in `pyproject.toml` and `src/aircraft_foundry/__init__.py`.
2. Add a `## x.y.z` section to `CHANGELOG.md`.
3. Push a tag: `git tag v0.9.1 && git push origin v0.9.1`.

The GitHub workflow then builds, tests and self-tests the Windows and macOS
apps, and publishes the release with `AircraftFoundry-Windows.zip`,
`AircraftFoundry-macOS.zip`, `AircraftFoundry-Portable.zip` and
`SHA256SUMS.txt`.

## Backwards compatibility

Saved designs and missions carry a `_format_version`. On load
(`core/compat.py`):

* **Migrations** translate older formats step by step (renamed or re-scaled
  fields).
* **Missing fields** get today's defaults, so a plane saved before a feature
  existed simply uses the default for it.
* **Unknown fields**, from a *newer* version, are ignored, so the file still
  opens.
* **Out-of-range numbers** are clamped, and options that no longer exist fall
  back to the default. Each adjustment is reported.

Tests keep a design saved in the 0.3 format, and one from a hypothetical
future version, loading and analysing correctly. Library rows and project
files written by older versions are tested too.

## Game controllers

Pair the controller in your computer's Bluetooth settings (or plug it in by
USB). It is picked up automatically, even while the app is running. The
**🎮 Controllers** page shows what is connected, the live sticks, triggers and
buttons, and the feel settings: dead zone, response curve, sensitivity, invert
pitch and rumble.

**Supported** (through SDL2, which maps them all to one standard layout):

* Xbox One / Series controllers;
* PlayStation DualShock 4 and DualSense (PS5);
* Nintendo Switch Pro Controller and **Joy-Cons**. Connect both Joy-Cons and
  they act as one controller; a single Joy-Con held sideways works with fewer
  controls.
* Most other gamepads SDL recognises.

Buttons follow **position**, not the printed letter. The bottom face button
(Xbox A, PlayStation Cross, Switch B) always means "confirm" / flaps.

**In the flight simulator**

| Control | Action |
|---|---|
| Left stick | pitch (pull back = nose up) and roll |
| LT / RT (ZL / ZR, L2 / R2) | rudder; also steers on the ground |
| Right stick up / down | throttle |
| D-pad up / down | throttle in 10% steps |
| LB / RB (L / R, L1 / R1) | pitch trim (hold) |
| South (A / Cross / Switch B) | flaps |
| East (B / Circle / Switch A) | landing gear |
| West (X / Square / Switch Y) | brakes (while held) |
| North (Y / Triangle / Switch X) | camera |
| Left-stick click | stability augmentation |
| Start / Options / + | pause |
| Back / Share / − | restart on the runway |

Moving the stick takes over from the autopilot, as on a real aircraft. The
controller **rumbles** on a stall, overstress, touchdown (stronger for harder
landings) and structural failure or crash.

**Everywhere else**

| Control | Action |
|---|---|
| D-pad / left stick | move between controls; left/right adjusts them |
| South button | press / tick |
| East button | back / close dialog |
| LB / RB | previous / next workspace page |
| Right stick | orbit the 3-D view |
| LT / RT | zoom |
| Start | fly the selected aircraft |

**macOS note.** If a controller is paired but not detected, allow Aircraft
Foundry under *System Settings → Privacy & Security → Input Monitoring*.
