# Changelog

## 0.14.0
- **Motor gliders:** sustainer (turbo) or self-launching engines on a retracting pylon; their weight, fuel
  and powered climb are part of the design check. In the simulator **N** (or the Engine button) extends,
  starts, stops and retracts the engine; an extended engine that is not running costs height.
- **Duo Discus T preset** (20 m two-seater, 18 kW sustainer).
- **Speed polar** for gliders in the design studio.
- **Fixed:** closing the design studio right after a change could crash the app.

## 0.13.0
- **Edit in 3-D:** click a part in the design studio's 3-D view to select it (it glows gold), and drag
  the wing-tip, wing-root, tail and engine handles directly in 3-D.
- **One-click fixes** for failed checks and unmet requirements (Fix buttons under the verdict; Undo
  reverses them).
- **Winch cable** laid out to just short of the far end of the runway (Lasham ~1.5 km); each winch
  driver differs a little.

## 0.12.1
- **Fixed: winch launches stalled the glider's wing** near the top, even before full rotation. The winch
  driver now holds launch speed (~1.6 x stall speed, about 105 km/h for a K-21) and eases the power as
  the cable steepens; if the glider does stall on the cable, the cable is released.
- **LOW SPEED - LOWER THE NOSE** warning on the winch.
- **Cockpit view** from the pilot's seat with an instrument panel (airspeed, artificial horizon,
  altimeter, variometer with 20 s average, compass, slip ball, g) and a yaw string for gliders.
- **Glider coaching** for launch, winch technique, soaring and landing.

## 0.12.0
- **Update banner:** the app checks at every start and every 4 hours, and shows a banner with the new
  version's summary and number of known problems (What's new & update / Update now / Later).
- **Version history:** `VERSIONS.md` and Help → Version history list what every version does and its
  known problems; What's new is shown once after updating.
- **Undo / redo** in the design studio (Ctrl+Z / Ctrl+Y; a whole drag is one step).
- **File → Open recent** and **View → Quick screenshot** (Ctrl+Shift+P).
- **Faster builds:** cached wheels and PyInstaller binaries, the release is published from the assemble
  job (no second download), fast zipping, superseded runs cancelled.

## 0.11.1
- **Fixed: the glider's tail broke at the start of a winch launch or aerotow.** Gliders now have a
  proper never-exceed speed (about 270-280 km/h; it was wrongly derived from the best-glide speed).
- **Glider wheels:** a main wheel and a tail wheel (or nose + main + tail, like the ASK 21) instead of
  airliner gear; the glider rests on a wing tip, which slides on its skid; a wing runner holds the
  wings level at the start of a launch.
- **K-21 preset** (two-seat trainer): 17 m, 17.95 m², +6.5 / -4 g, VNE 280 km/h, 600 kg.
- **More realistic winch launches:** the winch holds a sensible launch speed and backs off when fast,
  the hook back-releases at the top (or if the glider passes the winch), and a weak link protects
  the airframe.
- Fixed: gliders could be placed upside down on the runway.

## 0.11.0
- **Gliders.** A glider category and a "Glider kit" in the design studio: an 18 m sailplane with a
  laminar-flow wing (about 37:1 glide, 0.56 m/s minimum sink, stall ~61 km/h). In the simulator:
  winch launch or aerotow (L to launch / release), thermals to circle in, a variometer, and
  airbrakes on F.
- **More layout freedom:** drag engines forward / aft as well as along the wing; fine-tune the wing's
  height on the fuselage; set the outer engine pair's spacing.
- **Autosave and recovery:** unsaved work is copied every two minutes; after an unexpected exit the
  app offers to bring it back.
- **F1 in the simulator** shows every control.
- **Installer:** shows its version, keeps pip's temporary files on the target drive, and is published
  with each release (`.../releases/latest/download/install.ps1`) so web caches can't serve an old copy.

## 0.10.0
- **Choose where to fly, how big and how detailed.** Areas: Lasham (UK gliding), Innsbruck
  (Alpine valley), San Francisco Bay and Queenstown (New Zealand), each 40 or 80 km across,
  standard or high detail (30 m terrain, sharper imagery). Pick them on the simulator's Flight
  tab; maps download on first use and are kept in your data folder. Lasham is built in.
- **Runways are never buried by terrain** (terrain levelled under every runway, crossing runways
  share one level, runways drawn on top).
- **Realistic stalls and spins:** lift rounds off at CLmax, buffet warns, the nose drops; stalls
  with rudder become spins that follow the rudder and recover with the standard technique;
  T-tails can lock into a deep stall.
- **Crashes and break-ups:** structural failures and impacts break the aircraft into its parts,
  which tumble, bounce and slide over the terrain; gentle mishaps only break the gear / propellers.
- **Debriefs that help you fix it:** after a crash or failure: what happened, the numbers, how to
  fly it differently, and what to change in the design (with where to change it in the studio).
- **Wind tunnel:** strip-by-strip stall (watch where the wing stalls first), skin-friction and form
  drag, and the full lift curve with CLmax and stall angle.
- **More realistic aircraft:** livery, cabin windows, cockpit glazing or canopy, engine intakes.
- **Max positive and negative g** for the whole aircraft in the design studio; the larger sizes the
  structure, and the simulator breaks the wing beyond either.
- Fixed: crash on exit on Windows; releases retry if GitHub's network hiccups.

## 0.9.5
- **Windows installer fixes:** works with Windows PowerShell 5.1 (the one built into Windows, which
  could leave the release blank), and installing to a drive root such as `D:\` no longer fails
  with "The path is not of a legal form". Installer tests now run under PowerShell 5.1 too.
- **No more crash when closing on Windows:** once everything is saved, the app ends immediately
  instead of running native shutdown code that could crash ("stopped working").

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
