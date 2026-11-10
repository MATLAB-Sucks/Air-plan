# Aircraft Foundry versions

What each version does, and the problems known in it (with where they were fixed). The app shows the same notes in its update banner and **Help → Version history**.

## 0.12.0 — 2026-11-10

**Update banner, version history, undo / redo, faster builds.**

What it does:

- The app checks for updates at every start (and every 4 hours) and shows a banner with the new version's summary and known problems.
- What's new is shown once after updating; Help → Version history lists every version with its known problems (also VERSIONS.md).
- Design studio: undo / redo (Ctrl+Z / Ctrl+Y), a whole drag is one step.
- File → Open recent; View → Quick screenshot (Ctrl+Shift+P) of the whole window to Pictures.
- Faster GitHub builds: cached wheels and PyInstaller binaries, release published from the assemble job, superseded runs cancelled.

Known problems:

- Carried over from 0.11.1: winch launch speed can peak at 135-145 km/h; K-21 preset glides 32:1 (real 34:1).
- The San Francisco Bay 40 km standard map package is still missing.

## 0.11.1 — 2026-11-10

**Glider fixes and a K-21 preset.**

What it does:

- Glider tails no longer break at the start of a winch launch or aerotow (proper never-exceed speed).
- Glider wheels: main + tail wheel (K-21: nose + main + tail); wing-tip skids; a wing runner at launch.
- K-21 two-seat trainer preset.
- Winch launches: speed-controlled winch, automatic back-release at the top, weak link.

Known problems:

- Winch launch speed can peak at 135-145 km/h, faster than a typical launch (still below the K-21's 150 km/h placard).
- K-21 preset: 32:1 best glide and 0.74 m/s minimum sink (real ASK 21: 34:1 and 0.65 m/s).
- Released after targeted test runs (gliders, core, studio) rather than the full suite.

## 0.11.0 — 2026-11-10

**Gliders, freer layouts, autosave.**

What it does:

- Gliders: sailplane sizing, laminar glide polar, winch / aerotow launches, thermals, variometer, airbrakes.
- Design studio: engines forward / aft, fine wing height, outer engine pair spacing.
- Autosave every two minutes with recovery after an unexpected exit.
- F1 in the simulator lists every control.
- Installers show their version and are published with each release.

Known problems:

- Glider tail broke at the start of every winch launch or aerotow (dive speed derived from the best-glide speed). Fixed in 0.11.1.
- Gliders used airliner (tricycle) landing gear and could be placed upside down on the runway. Fixed in 0.11.1.
- Winch launches over-sped the glider and the hook never back-released. Fixed in 0.11.1.
- The San Francisco Bay 40 km standard map package failed to build.

## 0.10.0 — 2026-11-10

**Choose where to fly; stalls, spins and break-ups.**

What it does:

- Flying areas: Lasham, Innsbruck, San Francisco Bay, Queenstown; 40 or 80 km; standard or high detail (downloaded on first use).
- Runways are never buried by terrain.
- Realistic stalls, spins and T-tail deep stall.
- Crashes break the aircraft apart; debriefs explain what happened and how to fix it.
- Wind tunnel: strip-by-strip stall, profile drag, lift curve.
- Livery, cabin windows, cockpit glazing / canopy, engine intakes.
- Maximum positive and negative g for the whole aircraft.

Known problems:

- The one-line installer address could be served stale by web caches (old scripts kept running).
- The San Francisco Bay 40 km standard map package failed to build.

## 0.9.5 *(not published)* — 2026-11-10

**Windows exit crash fix (never published).**

What it does:

- End the process immediately once everything is saved (no native teardown crash on Windows).

Known problems:

- Never published: the release step hit a GitHub network timeout. Its fixes shipped in 0.10.0.
- First attempt crashed inside the exit call itself (untyped 64-bit handle).

## 0.9.4 — 2026-11-10

**Real-world Lasham; installer locations.**

What it does:

- Real terrain, satellite imagery and runways around Lasham.
- Installer: this computer / a drive / a folder; ready-to-run app or from source (with its own Python).
- Source copies can update themselves.

Known problems:

- Windows installer failed on Windows PowerShell 5.1 (blank release) and on drive roots like D:\ ("not of a legal form"). Fixed in 0.9.5 / 0.10.0.
- Windows could crash on exit (access violation after everything was saved). Fixed in 0.10.0.
- The macOS installer stopped with "AUTH[@]: unbound variable" (macOS's bash 3.2). Fixed on the main branch the same day.

## 0.9.3 — 2026-11-10

**One-command install, small screens, full screen.**

What it does:

- One-line installers for Windows, macOS and Linux.
- Window fits small laptop screens; every side panel scrolls.
- Collapsible side panels and full screen (F11).

Known problems:

- macOS installer failed without a GitHub token (bash 3.2 empty array). Fixed after 0.9.4.
- "STALL" warning while parked and a huge endurance figure at idle (fixed in this release).

## 0.9.2 — 2026-11-09

**First published release with the self-updater.**

What it does:

- Self-updater, game controllers, backwards-compatible files, OpenGL check, two-panel window.

Known problems:

- The update button was disabled for copies run from a source zip (fixed in 0.9.4).
- Builds only passed the Windows self-test with Mesa software OpenGL on GitHub's GPU-less machines.

## 0.9.1 *(not published)* — 2026-11-09

**Never published.**

What it does:

- Controller and updater work in progress.

Known problems:

- The tag was built with a broken workflow, so no release was published.

## 0.9.0 *(not published)* — 2026-11-08

**Only distributed as a source zip.**

What it does:

- Design studio, wind tunnel, flight simulator with autopilot and four airports, stunt planes.

Known problems:

- No packaged release (workflow broken); the source copy couldn't use the update button.
