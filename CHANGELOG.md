# Changelog

## 0.9.4
- **Real-world flying around Lasham** (the gliding capital): real terrain heights, Sentinel-2
  satellite imagery, and the real runways of Lasham, RAF Odiham, Farnborough, Blackbushe, Popham
  and nearby strips from OpenStreetMap. Choose the world on the simulator's Flight tab; the
  fictional test range is still there. Map data is built on GitHub by `tools/build_world.py`.
- **Installer: choose where and how.** This computer, a USB / external drive (portable: planes
  stay on the drive) or any folder; the ready-to-run app or from source. Source installs reuse
  this computer's Python 3.11 or put a self-contained Python inside the install folder, so they
  work first time and from any drive.
- **Fixed: the macOS installer stopped with "AUTH[@]: unbound variable"** (macOS's bash 3.2).
- **Updates for source copies**: the update button now works for copies unpacked from a zip
  (not just installed apps and git checkouts).
- Fixed: a crash on exit on Windows when running from source; autopilot "Land now" intercepts
  directly when already lined up.

## 0.9.3
- **One-command install** for Windows (`irm …/install.ps1 | iex`) and macOS / Linux
  (`curl -fsSL …/install.sh | bash`). It downloads the latest release, verifies its
  checksum and adds shortcuts. Run it again to update; saved planes are kept.
- **Small screens** (1366×768, or 1920×1080 at 150 % scaling): the window opens sized to
  the screen, every side-panel page scrolls, the side panels can be narrower, and the 3-D
  view keeps a fair share of the width.
- **Collapsible side panels and full screen.** Hide either panel (its ×, the ◧ / ◨ toolbar
  buttons, the View menu, or Ctrl+[ / Ctrl+]). **F11** shows only the current view (flight
  simulator, wind tunnel, studio or 3-D model) filling the screen, and **F11 / Esc** brings
  everything back. Panel choices are remembered.
- Simulator HUD: no "STALL" warning while parked, and no huge endurance figure at idle.
- Every release is now installed on clean Windows and macOS machines with those
  commands, and self-tested, before it counts as good.

## 0.9.2
- First published release: the build workflow is fixed (Windows PyInstaller exit-crash
  workaround, numba TBB conflict), so version tags now produce downloadable releases.
- **No more crash without OpenGL 3.2** (virtual machines, Remote Desktop, old drivers): the 3-D
  view shows an explanation instead, and everything else keeps working.
- Clearer update messages: "no release published yet" instead of "404", and the time
  to try again when GitHub's hourly limit is used up.
- Includes everything from 0.9.0 and 0.9.1 (updates, compatibility, controllers, design
  studio fixes, pages that follow the selected plane).

## 0.9.1
- **Fixed: dragging parts in the design studio.** Parts could not be dragged out of the
  parts bin: a Qt setting silently disabled dragging. A real-mouse drag test now guards it.
- **Every page follows the selected plane.** The design studio, wind tunnel and flight
  simulator open with the plane selected on the left, switch when you choose another, and
  refresh after edits on the Parameters page. Page titles show which plane you're on.
- **Design studio saves into the plane you're editing:** *Apply changes to '<plane>'*, or
  *Save as new plane*. Switching planes with unapplied changes asks first.
- Flying or testing an unsaved studio design shows it as "<plane> (studio)" until you
  choose a plane again.

## 0.9.0
- **Check for updates** (status bar, or Help menu): installs new versions from GitHub
  Releases, verified by SHA-256. Your saved planes, projects and settings are kept, and
  the design library is backed up first. Source checkouts update with `git pull`.
- **Backwards compatibility**: saved designs and missions carry a format version and are
  migrated on load. Missing fields get defaults, fields from newer versions are ignored,
  and out-of-range values are adjusted and reported. Designs from 0.3 onwards open.
- **Game controllers** over Bluetooth or USB: Xbox, PlayStation (DualShock 4 / DualSense)
  and Nintendo Switch (Pro Controller, Joy-Con pairs). They fly the simulator (with
  rumble) and navigate the rest of the app. There is a new 🎮 Controllers page.

## 0.8.0
- Two-panel workspace, a calmer theme, Kerbal-style design studio, selectable
  requirements, G rating requirement, and strength and material per component.

## 0.7.0
- Stunt planes (aerobatic category, ±10 g), light-aircraft weights, nose propeller.

## 0.6.0
- Design studio (drag handles, "will it fly?") and the vortex-lattice wind tunnel.

## 0.5.0
- Autopilot, a 120 km world with four airports, fuel, structural failure, weather and a
  flight log.
