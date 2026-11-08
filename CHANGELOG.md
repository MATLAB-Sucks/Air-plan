# Changelog

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
