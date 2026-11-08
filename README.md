# Aircraft Foundry

A standalone desktop application for procedural conceptual aircraft design.
It turns mission requirements into sized aircraft configurations, then
evaluates, compares and evolves them. Every aircraft is generated as a
structured assembly in which each part has its own identity and its
relationships to other parts:

* skins, ribs, spars, frames and longerons;
* nacelles, pylons and landing gear;
* attachment fittings.

Designs export to STEP, STL, OBJ and JSON.

Technology: Python 3.11, PySide6, VTK, NumPy/SciPy and CadQuery/OpenCascade.
Projects and design parameters are saved as JSON.

**New in 0.7:**

* **Stunt planes:** an aerobatic category with ±10 g structure, a symmetric
  wing, near-full-span ailerons, big tails and a nose propeller, sized to the
  Extra 330 class.
* Light-aircraft weight equations, and structure that now follows each
  design's own g rating.

See [docs/AEROBATIC.md](docs/AEROBATIC.md).

**New in 0.6:**

* **Design studio:** drag and resize parts, add tails and engines, resize
  control surfaces, and get a live "will it fly?" verdict; then fly the
  result.
* **Wind tunnel:** vortex-lattice airflow with streamlines and smoke,
  pressure colouring, and a view of where the airframe would break at any g.

See [docs/DESIGN_STUDIO_AND_WIND_TUNNEL.md](docs/DESIGN_STUDIO_AND_WIND_TUNNEL.md).

**New in 0.5:**

* An autopilot that can take off, land, fly continuous low-fuel circuits and
  follow waypoint flight plans.
* A 120 km world with four airports.
* Fuel burn, structural failure (over-g or overspeed), wind and turbulence.
* A flight-data recorder with CSV export.

See [docs/SIMULATOR.md](docs/SIMULATOR.md).

**New in 0.4:**

* **Supersonic flight.** Concorde-style deltas and X-59-style low-boom
  shapes, with area-rule wave drag.
* **Sonic-boom prediction.** Whitham's theory, including Mach-cutoff
  boomless cruise.
* **Noise limits.** New mission limits for the ground boom, airport noise and
  cabin noise, which the design meets through bypass ratio, insulation and
  overland speed. See [docs/SUPERSONIC_AND_NOISE.md](docs/SUPERSONIC_AND_NOISE.md).
* **✈ Fly button.** Flight-test any design in a 6-degree-of-freedom simulator,
  with flying-qualities analysis. See [docs/SIMULATOR.md](docs/SIMULATOR.md).

**New in 0.3:** genuinely different aircraft. The generator now offers, and
correctly analyses:

* flying wings;
* blended wing bodies;
* canards;
* twin-booms;
* V-tails and H-tails;
* single and twin pushers.

This sits alongside conventional layouts. See [docs/LAYOUTS.md](docs/LAYOUTS.md).
It also fixes oversized tails on aircraft with a small cabin and a big wing.

**New in 0.2:**

* **Portable USB edition:** a Windows build and a Mac build on one stick,
  sharing one design library. See [docs/PORTABLE_USB.md](docs/PORTABLE_USB.md).
* **Control surfaces:** ailerons, flaps, elevators and rudder, each a real
  separate solid that deflects about its hinge line.
* **Structural sizing and stress analysis,** with a Stress view in the 3D
  viewport.

## Requirements

* macOS 12 Monterey or later, on an **Intel (x86_64)** or Apple Silicon Mac.
* Python **3.11**. Use the python.org universal2 installer or `brew install python@3.11`.
* About 2 GB of disk space for the environment.

No compiler or Xcode toolchain is needed. Every dependency installs from a
pre-built wheel (see "Dependency notes" below).

## Setup (from source)

```bash
cd aircraft-foundry
./scripts/setup_macos.sh     # creates .venv; picks the Intel or ARM lock file automatically
./scripts/run.sh             # starts the application
```

Manual equivalent on an Intel Mac:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install --only-binary :all: -r requirements/lock-macos-intel-py311.txt
pip install --no-deps -e .
python -m aircraft_foundry
```

To check the install:

```bash
pytest                                   # 161 tests; the GUI test is skipped unless AF_GUI_TESTS=1
AF_GUI_TESTS=1 pytest tests/test_gui.py  # drives the real window: generate, edit, pick, evolve, save
python -m aircraft_foundry --self-test   # opens, renders, evaluates, then exits
```

## Using the application

**Mission & generation** (left panel). Enter:

* payload, range, cruise Mach and altitude;
* field length, approach speed and span limit;
* the optimisation objective.

The derived cabin (passengers, seats abreast, fuselage diameter) updates as you
type. Press **Generate candidates**: every viable configuration is sized, then
the design space around each one is explored.

**Candidate comparison** (bottom panel):

* Each row shows a planform thumbnail, MTOW, fuel, L/D, static margin and
  constraint status.
* Columns are sortable. Hover over Status to see which constraints are
  violated.
* Double-click a row to load that candidate.

**3D viewport:**

* Drag to rotate, right-drag or scroll to zoom, middle-drag to pan.
* View modes: Solid, Transparent, Wireframe, Structure.
* Layer toggles: skins, structure members, fittings, engines and gear.
* Click a part to select it.

**Inspector** (right panel):

* The component tree is grouped by assembly.
* Each component shows its ID, type, placement and parameters, plus
  clickable relationships to connected parts.
* With nothing selected, it shows the constraint table and mass breakdown.

**Parameters** (right panel):

* Every design parameter, grouped.
* An edit regenerates only the affected components and re-evaluates the
  design immediately.
* ★ marks the variables that evolution optimises.

**Evolve selected:**

* Select one or more candidates and press Evolve.
* SciPy differential evolution starts from the selected designs.
* The configuration (topology) is held fixed, and each configuration is
  evolved separately.

**File ▸ Export** (⌘E):

* Formats: STEP, STL, OBJ and/or JSON.
* Scope: the complete assembly, or only the selected component with its
  sub-components.
* Options: include or exclude internal structure; write one file per
  component.

**Controls** (Parameters ▸ Controls):

* Set aileron, flap, elevator and rudder span and chord, and deflect each
  surface.
* Each control surface is a separate solid, cut out of its parent skin and
  attached by hinge fittings.
* Flap layout drives the landing CLmax.
* The actual control-surface areas feed the wing-weight equation.
* A constraint checks that every hinge line clears the rear spar.

**Structures** (right-hand tab, Stress view mode, Parameters ▸ Structure):

* The wing box is sized station by station:
  * spanwise lift by Schrenk's method, with inertia relief from structure,
    fuel and engines;
  * MTOW and zero-fuel load cases;
  * Farrar stringer-panel covers, so rib pitch matters;
  * spar webs for shear plus torsion, and for buckling.
* The fuselage is sized for pressurisation hoop stress (fatigue and
  ultimate), manoeuvre bending, and Shanley frame stiffness.
* Thicknesses are rounded up to standard gauges, and every rib, spar, frame,
  longeron, fitting and control surface gets loads and a margin of safety.
  Click a part to see them in the Inspector.
* The Structures tab plots bending, shear and thickness, and shows the sized
  mass breakdown beside the statistical (Raymer) mass.
* Materials (aluminium alloys, Al-Li, titanium, CFRP), load factor and
  safety factor are editable.
* Set *weight method = structural* to close the take-off weight with the sized
  structure instead of the statistics.

**Design library** (bottom tab, ⌘L / Ctrl+L to save):

* A shared database of saved aircraft, with search, tags, notes and thumbnails.
* On a portable stick it is shared between the Windows and Mac builds.

**File ▸ Save** writes an `.afproj` file. It stores only the mission and the
parameters. Geometry and evaluation results are regenerated when the file is
loaded.

### Command line (no GUI)

```bash
aircraft-foundry-cli generate --mission examples/regional_turboprop.json --out results --evolve 2
aircraft-foundry-cli export results/AF-001-01.json --out exports --formats step stl obj json --structure
```

## Portable USB edition (Windows + Mac)

See [docs/PORTABLE_USB.md](docs/PORTABLE_USB.md).

* The easiest route: run the included GitHub Actions workflow. It builds and
  tests both versions on real Windows and Intel-Mac machines and produces a
  ready-to-copy USB folder.
* Locally: `scripts/setup_windows.ps1`, `packaging/build_windows.ps1` and
  `packaging/assemble_usb.py`.

## Packaging a standalone .app

Run this once the source version works on your Mac:

```bash
./packaging/build_macos_app.sh   # -> dist/Aircraft Foundry.app, then runs --self-test
```

* Build on the target architecture. An Intel Mac produces an x86_64 app.
* A universal2 app is not possible, because the VTK, NumPy and OpenCascade
  wheels are each single-architecture.
* The app is about 1.4 GB, mostly OpenCascade and VTK.
* The app is unsigned. For local use, right-click it and choose Open the first
  time. To distribute it, sign and notarise it as the build script describes.

## Dependency notes (why the pins are exact)

The versions were checked against the actual wheel files on PyPI. The full
46-package tree resolves for `x86_64-apple-darwin` using pre-built wheels only
(see `requirements/lock-macos-intel-py311.txt`). Several current releases
would break an Intel install:

| Package | Pinned | Reason |
|---|---|---|
| nlopt | 2.9.1 | 2.10 and 2.11 have **no Intel macOS wheel**, so pip would try to build from source |
| numba / llvmlite | 0.62.1 / 0.45.1 | 0.63+ / 0.46+ **dropped Intel macOS**; CadQuery 2.8 requires numba |
| numpy | 2.3.5 | numba 0.62 requires numpy < 2.4; numpy 2.5 needs Python ≥ 3.12 |
| scipy | 1.16.3 | 1.18 needs Python ≥ 3.12 |
| vtk | 9.6.2 | cadquery-ocp 7.9.3.1.1 requires exactly this version, and the renderer shares it |
| PySide6-Essentials | 6.9.3 | 6.10+ needs macOS 13 and 6.12 needs macOS 14; 6.9 keeps macOS 12 support |

## Project layout

```
src/aircraft_foundry/
  core/            design engine (no GUI): mission, parameters, components, design,
                   synthesis (sizing), evaluator, structures (stress & sizing),
                   generator (generation + evolution), project
  library/         shared SQLite design library
  portable.py      portable (USB) vs per-user data location
  geometry/        airfoil, planform, fuselage, arrangement, loft (shared representation),
                   builder (component generators), cad (OpenCascade solids)
  numerics/        ISA atmosphere, aerodynamics, statistical weights, materials
  structural_vis/  view modes, layers, colours, structure summaries
  rendering/       VTK scene (incremental), lighting and cameras
  fileio/          STEP, STL, OBJ, JSON export and import
  app/             PySide6 interface
tests/             pytest suite
requirements/      direct requirements and wheel-only locks (macOS Intel/ARM, Windows x64, Linux)
packaging/         PyInstaller spec, macOS/Windows build scripts, USB assembler
.github/workflows/ automated Windows + Intel-Mac build of the USB edition
examples/          example missions
docs/ARCHITECTURE.md
```

## Engineering methods and their limits

This is a **conceptual-design** tool. It is suitable for trade studies and for
ranking configurations, not for certification or detailed design.

**Weights:**

* The structural and landing-gear groups use Raymer's cargo/transport
  statistical equations.
* Three groups use fits checked against published narrow-body and wide-body
  data instead:
  * engine weight, because Raymer's power law overestimates large turbofans;
  * systems, because Raymer's flat 0.17·W0 overestimates wide-bodies by about 2×;
  * furnishings and operator items, which Raymer omits.

**Take-off weight:** payload, crew, empty weight and fuel are solved
together to a self-consistent value using SciPy `brentq`.

**Aerodynamics:**

* Drag is built up component by component: skin friction, form factors and
  interference factors, plus aft-fuselage upsweep drag.
* Span efficiency uses Obert's correlation.
* Wave drag uses the Korn equation.
* The lift-curve slope uses the DATCOM method.

**Stability:** the neutral point is estimated including downwash and the
destabilising effect of the fuselage. It is compared with a centre of gravity
built up from the components at maximum take-off weight.

**Constraints checked:**

* wingspan, approach speed and take-off field length;
* one-engine-inoperative climb gradient and cruise thrust margin;
* static margin and tail volume coefficients;
* wing fuel capacity and drag-divergence margin;
* tip-back angle and nose-gear load fraction;
* cabin length;
* engine ground clearance and propeller–fuselage clearance.

**Sanity check:** an A320-class mission sizes to about 82–85 t MTOW with a
cruise L/D of about 15.7. The real aircraft is about 78 t, so expect results
to land within roughly 10–15 % of real aircraft.

**Structural analysis:**

* The wing box is analysed station by station.
* The fuselage is treated as a pressurised, stringer-stiffened beam.
* Tails use statistical weights only; they show grey in the Stress view.
* The sized structural masses agree with the statistical masses to within
  about 20% from 7 t to 446 t MTOW. This is an independent cross-check, not a
  calibration.
* Margins are computed at standard gauges. Because the structure is sized to
  the loads, most members are close to their allowables.

**Known limitations:**

* Engines are scaled statistically from their thrust rather than modelled.
* Flaps are modelled as plain hinged flaps: there are no Fowler tracks or slats.
* Supersonic designs are sized by optimisation and validated against
  Concorde. Large low-boom (X-59-style) airliners and strict airport-noise
  limits for supersonic aircraft often come out infeasible. That reflects the
  real open problems.
* The structural model has no fatigue spectrum, no aeroelastic analysis and no
  gust load cases.
* "Freeze gauges, then re-check after edits" is not implemented yet: today the
  structure is always re-sized to the current loads.
* Fuselage frames are uniform rings.
* Structural members are conceptual layouts, not stress-sized parts.
