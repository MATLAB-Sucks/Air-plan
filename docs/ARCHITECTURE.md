# Architecture

```
                 ┌──────────────────────── app/ (PySide6) ─────────────────────────┐
                 │ mission_panel  gallery  viewport  inspector  parameter_editor  │
                 │ export_dialog  main_window  tasks (QThread workers)            │
                 └───────┬───────────────────────┬────────────────────────┬───────┘
                         │ edits / requests      │ AircraftDesign         │ ExportRequest
                         ▼                       ▼                        ▼
 ┌──────────── core/ (no Qt, no VTK) ──────┐  ┌ rendering/ (VTK) ┐  ┌ fileio/ ─────────────┐
 │ mission ─► synthesis ─► generator       │  │ scene: actor per │  │ STEP (cadquery/OCC)  │
 │            (sizing)     (LHS + SciPy DE) │  │ component, diff  │  │ STL, OBJ, JSON       │
 │ parameters ─► design ◄── evaluator      │  │ by fingerprint   │  └──────────┬───────────┘
 │  (schema)     (components, ChangeSet)   │  └────────┬─────────┘             │
 │ project (JSON)                          │           │ appearance()          │
 └───────────────┬─────────────────────────┘  ┌────────▼─────────┐             │
                 │ generators / arrangement   │ structural_vis/  │             │
                 ▼                            └──────────────────┘             │
 ┌──────────── geometry/ ───────────────────────────────────────────────────────▼─┐
 │ arrangement (where everything sits) ─► builder (component generators)        │
 │ planform, fuselage, airfoil ─► Loft (shared section representation)          │
 │ Loft ─► loft_to_mesh (VTK/STL/OBJ)       Loft ─► cad.loft_to_solid (STEP)     │
 └───────────────────────────────────────────────────────────────────────────────┘
 numerics/: atmosphere, aerodynamics, weights (used by core.evaluator)
```

## One source of truth

* **Parameters** (`core/parameters.py`) are the only editable state. Every
  field carries bounds, units, a label and whether the optimiser may vary
  it; the GUI editor, validation and the evolution bounds all read this.
* **Arrangement** (`geometry/arrangement.py`) turns parameters into
  placed surfaces, engines and gear legs. The geometry builder *and* the
  evaluator use it, so the aircraft that is analysed is the aircraft drawn.
* **Loft** (`geometry/loft.py`) is the shared shape representation: ordered
  cross-sections. It is triangulated for the viewport/STL/OBJ and lofted into
  OpenCascade B-rep for STEP. The test suite checks the two agree
  (bounding boxes within 5 cm, volumes within a few per cent).

## Structured components and dependency tracking

`AircraftDesign` holds `Component`s with a stable dotted id
(`wing.right.rib.07`), a type, local geometry, a placement, typed relations
(`part_of`, `attached_to`, `mounted_on`, `bounded_by`, `connects`) and the
parameters they were generated from.

Each generator in `geometry/builder.py` declares the parameter groups it
reads. `design.set_parameter("wing.span", 40)` re-runs only generators that
depend on `wing` (wing skins, ribs, spars, wing-root fittings, engines and
pylons, landing gear) and returns a `ChangeSet` of added/updated/removed ids
found by fingerprint comparison. Ribs, spars and attachment points are
computed from the same planform, so they follow every wing change.

## Incremental rendering

`rendering/scene.py` keeps one VTK actor per component. Geometry is uploaded
in the component frame and positioned by the actor's matrix:
unchanged components are untouched, moved ones get a new matrix only, and
only geometry whose fingerprint changed is re-uploaded. Switching between
candidates reuses actors wherever components are identical.

## Threading

Generation, evolution and export run in `QThread` workers
(`app/tasks.py`) with progress callbacks and cooperative cancellation. The
engine functions take plain `progress`/`cancel` callables, so the CLI and
tests call them identically without Qt.

## Control surfaces

`Arrangement.control_surfaces` defines every aileron, flap, elevator and rudder
once. The same definitions drive three things:

* **Geometry:** the parent skin is split spanwise and cut at the hinge line,
  and each movable surface is lofted aft of the hinge. Deflection is a
  rotation about the true swept hinge axis.
* **Analysis:** flapped area sets CLmax, and control-surface area feeds the
  wing weight.
* **Structures:** hinge moments and hinge-fitting loads.

Every generator declares its parameter dependencies, and a test enforces that
those declarations cover the parameter groups each component is built from.

## Structures

`core/structures.py` sizes the wing box and fuselage from the arrangement.
Results are keyed by component ID:
* `member_results` holds loads, gauges and margins;
* `utilisation` holds the ratio of stress to allowable.

The renderer colours members from `utilisation` in Stress mode, and the
inspector shows `member_results`. The analysis is vectorised NumPy (about
10 ms), which makes it cheap enough to run inside the weight closure when the
structural weight method is selected.

## Data location and library

`portable.locate()` decides where all data lives. The library
(`library/store.py`) is a single SQLite file with no Qt dependency, designed
for removable media. The GUI uses an INI settings file inside the data folder,
so on a USB stick nothing is written to the host computer.

## Layouts (topologies)

`Arrangement` describes any layout as a generic list of:

* `PlacedSurface` objects, each with a key, role
  (`wing | htail | canard | fin | v_tail`), planform, placement, sides,
  parameter group and parent;
* `Body` objects (fuselage or crew pod, tail booms).

Nothing downstream special-cases a layout:

* the builder makes skins, ribs, spars, fittings and control surfaces for
  every surface;
* the evaluator sums drag and weight over surfaces and bodies;
* the neutral point uses each pitch surface with downwash, or upwash for a
  canard, and a V-tail's cos²/sin² area split.

Layout-specific choices (which surfaces exist, where the fins go, which
constraints apply) live in `Arrangement` and in `core/synthesis.py`. That
keeps adding a new layout to two places. Component IDs stay stable: a
conventional aircraft still has `wing.right`, `htail.left` and `vtail`.
