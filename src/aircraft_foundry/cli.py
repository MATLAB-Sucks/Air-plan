"""Command line: run the design engine and exporters without the GUI.

    aircraft-foundry-cli generate --mission mission.json --out results/
    aircraft-foundry-cli export design.json --out exports/ --formats stl obj step
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core.design import AircraftDesign
from .core.generator import EvolutionSettings, evolve, generate_candidates
from .core.mission import MissionRequirements
from .core.project import Project
from .fileio.common import ExportSelection
from .fileio.exporter import ExportRequest, run_export
from .fileio.json_io import export_design_json, import_design_json


def _print_progress(f, msg):
    print(f"\r[{f * 100:5.1f}%] {msg[:70]:<70}", end="", file=sys.stderr, flush=True)


def cmd_generate(a) -> int:
    mission = MissionRequirements.load(a.mission) if a.mission else MissionRequirements()
    cands = generate_candidates(mission, a.count, a.seed, a.samples, progress=_print_progress)
    print(file=sys.stderr)
    if a.evolve:
        cands += evolve(cands[: a.evolve], mission, EvolutionSettings(generations=a.generations, seed=a.seed),
                        progress=_print_progress)
        print(file=sys.stderr)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    proj = Project(mission, cands, cands[0].id if cands else None)
    proj.save(out / "project.afproj")
    rows = []
    for c in sorted(cands, key=lambda c: c.evaluation.score):
        ev = c.evaluation
        rows.append({"name": c.name, "configuration": c.config_label, "feasible": ev.feasible, "score": ev.score,
                     "mtow_kg": ev.metrics.get("mtow_kg"), "message": ev.message})
        export_design_json(out / f"{c.name}.json", AircraftDesign(c.params, c.name), mission, ev)
    (out / "summary.json").write_text(json.dumps(rows, indent=2))
    for r in rows:
        print(f"{r['name']:16s} {'OK ' if r['feasible'] else '-- '} score {r['score']:12,.0f}  "
              f"MTOW {r['mtow_kg'] or 0:10,.0f} kg  {r['configuration']}")
    return 0


def cmd_export(a) -> int:
    design, mission = import_design_json(a.design)
    req = ExportRequest(Path(a.out), tuple(a.formats),
                        ExportSelection(a.component or None, a.structure), a.per_component, design.name)
    paths = run_export(req, design, mission, None, _print_progress)
    print(file=sys.stderr)
    for p in paths:
        print(p)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aircraft-foundry-cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate (and optionally evolve) candidates for a mission")
    g.add_argument("--mission", help="mission JSON (defaults to the built-in example)")
    g.add_argument("--out", required=True)
    g.add_argument("--count", type=int, default=8)
    g.add_argument("--samples", type=int, default=24)
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--evolve", type=int, default=0, help="evolve the best N candidates")
    g.add_argument("--generations", type=int, default=20)
    g.set_defaults(fn=cmd_generate)
    e = sub.add_parser("export", help="export a design JSON to STEP/STL/OBJ/JSON")
    e.add_argument("design")
    e.add_argument("--out", required=True)
    e.add_argument("--formats", nargs="+", default=["stl", "obj", "json"], choices=["step", "stl", "obj", "json"])
    e.add_argument("--component", nargs="*", help="component ids (default: whole aircraft)")
    e.add_argument("--structure", action="store_true", help="include internal structure")
    e.add_argument("--per-component", action="store_true")
    e.set_defaults(fn=cmd_export)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
