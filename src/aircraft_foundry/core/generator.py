"""Candidate generation (Latin-hypercube exploration) and evolution
(SciPy differential evolution seeded from selected candidates)."""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.optimize import differential_evolution
from scipy.stats import qmc

from .evaluator import Evaluation, evaluate
from .mission import MissionRequirements
from .parameters import AircraftParameters, GROUP_TYPES, evolvable_paths, field_meta
from .synthesis import baseline, complete_design, configurations_for

Progress = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


@dataclass
class Candidate:
    params: AircraftParameters
    evaluation: Evaluation | None = None
    name: str = ""
    origin: str = "generated"
    parents: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def config_key(self) -> tuple[str, ...]:
        c = self.params.configuration
        return (c.layout, c.propulsion, c.engine_layout, c.tail_type, c.wing_position)

    @property
    def config_label(self) -> str:
        from ..geometry.arrangement import Arrangement
        c = self.params.configuration
        arr = Arrangement(self.params)
        n = len(arr.engines)
        mount = {"wing": "wing-mounted", "rear_fuselage": "rear-fuselage", "upper_aft": "buried",
                 "pusher": "pusher"}[c.engine_layout]
        eng = f"{n}x {mount} {c.propulsion}"
        tail = {"conventional": "conventional tail", "t_tail": "T-tail", "v_tail": "V-tail",
                "twin_fin": "twin fins", "none": "no fins"}[arr.tail_type]
        if c.layout == "conventional":
            return f"{eng}, {c.wing_position} wing, {tail}"
        name = {"canard": "Canard", "flying_wing": "Flying wing", "blended_wing_body": "Blended wing body",
                "twin_boom": "Twin-boom"}[c.layout]
        return f"{name}: {eng}, {tail}"

    def evaluate(self, mission: MissionRequirements) -> Evaluation:
        self.evaluation = evaluate(self.params, mission)
        return self.evaluation

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "origin": self.origin, "parents": self.parents,
                "parameters": self.params.to_dict()}

    @classmethod
    def from_dict(cls, d: dict, mission: MissionRequirements | None = None) -> "Candidate":
        c = cls(AircraftParameters.from_dict(d["parameters"]), None, d.get("name", ""), d.get("origin", "loaded"),
                d.get("parents", []), d.get("id") or uuid.uuid4().hex[:10])
        if mission is not None:
            c.evaluate(mission)
        return c


# ---------------------------------------------------------------- variable encoding
def variable_bounds(path: str) -> tuple[float, float]:
    group, name = path.split(".")
    m = field_meta(GROUP_TYPES[group], name)
    return m["lo"], m["hi"]


def encode(params: AircraftParameters, paths: list[str]) -> np.ndarray:
    return np.array([float(params.get(p)) for p in paths])


def decode(base: AircraftParameters, paths: list[str], x: np.ndarray, mission: MissionRequirements
           ) -> AircraftParameters:
    q = base.copy()
    for path, v in zip(paths, x):
        lo, hi = variable_bounds(path)
        q.set(path, float(np.clip(v, lo, hi)))
    return complete_design(q, mission)


def _distance(a: np.ndarray, b: np.ndarray, span: np.ndarray) -> float:
    return float(np.linalg.norm((a - b) / span) / math.sqrt(len(a)))


def _select_diverse(cands: list[Candidate], n: int, paths: list[str]) -> list[Candidate]:
    ranked = sorted(cands, key=lambda c: (not c.evaluation.feasible, c.evaluation.score))
    span = np.array([max(abs(c), 1e-6) for c in encode(ranked[0].params, paths)])
    chosen: list[Candidate] = []
    seen_cfg = set()
    for c in ranked:  # best of every configuration first
        if c.config_key not in seen_cfg and c.evaluation.converged:
            chosen.append(c)
            seen_cfg.add(c.config_key)
    for c in ranked:
        if len(chosen) >= n:
            break
        if c in chosen or not c.evaluation.converged:
            continue
        x = encode(c.params, paths)
        if all(_distance(x, encode(o.params, paths), span) > 0.04 for o in chosen if o.config_key == c.config_key):
            chosen.append(c)
    return sorted(chosen[:n], key=lambda c: (not c.evaluation.feasible, c.evaluation.score))


# ---------------------------------------------------------------- generation
def generate_candidates(mission: MissionRequirements, count: int = 8, seed: int = 0, samples_per_config: int = 24,
                        spread: float = 0.18, progress: Progress | None = None,
                        cancel: CancelCheck | None = None) -> list[Candidate]:
    """Size every viable configuration, then explore around each baseline."""
    problems = mission.validate()
    if problems:
        raise ValueError("; ".join(problems))
    configs = configurations_for(mission)
    rng_seed = np.random.SeedSequence(seed)
    pool: list[Candidate] = []
    paths = evolvable_paths(AircraftParameters())
    total = len(configs) * (samples_per_config + 1)
    done = 0
    for ci, cfg in enumerate(configs):
        if cancel and cancel():
            break
        base = baseline(mission, cfg)
        b = Candidate(base, name="", origin="baseline sizing")
        b.evaluate(mission)
        pool.append(b)
        done += 1
        x0 = encode(base, paths)
        lo = np.array([max(variable_bounds(pth)[0], v * (1 - spread)) for pth, v in zip(paths, x0)])
        hi = np.array([min(variable_bounds(pth)[1], v * (1 + spread)) for pth, v in zip(paths, x0)])
        hi = np.maximum(hi, lo + 1e-9)
        sampler = qmc.LatinHypercube(d=len(paths), seed=np.random.default_rng(rng_seed.spawn(1)[0]))
        for x in qmc.scale(sampler.random(samples_per_config), lo, hi):
            if cancel and cancel():
                break
            c = Candidate(decode(base, paths, x, mission), origin="design-space exploration")
            c.evaluate(mission)
            pool.append(c)
            done += 1
            if progress:
                progress(done / total, f"Exploring {cfg.propulsion} / {cfg.engine_layout} / {cfg.tail_type}")
    chosen = _select_diverse(pool, count, paths)
    for i, c in enumerate(chosen):
        c.name = f"AF-{seed:03d}-{i + 1:02d}"
    return chosen


# ---------------------------------------------------------------- evolution
@dataclass
class EvolutionSettings:
    generations: int = 25
    population: int = 20
    seed: int = 1
    search_radius: float = 0.30  # +/- fraction around the seed designs
    mutation: tuple[float, float] = (0.5, 1.0)
    recombination: float = 0.7
    keep: int = 4  # candidates returned per configuration


def evolve(seeds: list[Candidate], mission: MissionRequirements, settings: EvolutionSettings | None = None,
           progress: Progress | None = None, cancel: CancelCheck | None = None) -> list[Candidate]:
    """Evolve selected designs.  Topology is held fixed: seeds are grouped by
    configuration and each group is optimised separately."""
    s = settings or EvolutionSettings()
    groups: dict[tuple, list[Candidate]] = {}
    for c in seeds:
        groups.setdefault(c.config_key, []).append(c)
    out: list[Candidate] = []
    rng = np.random.default_rng(s.seed)
    for gi, (key, members) in enumerate(groups.items()):
        base = members[0].params
        paths = evolvable_paths(base)
        xs = np.array([encode(m.params, paths) for m in members])
        lo = np.maximum([variable_bounds(p)[0] for p in paths], xs.min(0) * (1 - s.search_radius))
        hi = np.minimum([variable_bounds(p)[1] for p in paths], xs.max(0) * (1 + s.search_radius))
        hi = np.maximum(hi, lo + 1e-9)
        n_pop = max(s.population, len(members) + 2, 5)
        init = np.vstack([xs, rng.uniform(lo, hi, size=(n_pop - len(members), len(paths)))])
        init = np.clip(init, lo, hi)
        archive: dict[tuple, Candidate] = {}

        def objective(x: np.ndarray) -> float:
            key_x = tuple(np.round(x, 6))
            if key_x not in archive:
                c = Candidate(decode(base, paths, x, mission), parents=[m.id for m in members])
                c.evaluate(mission)
                archive[key_x] = c
            return archive[key_x].evaluation.score

        gen = [0]

        def callback(intermediate_result) -> bool:
            gen[0] += 1
            if progress:
                frac = (gi + gen[0] / s.generations) / len(groups)
                progress(min(frac, 1.0), f"Generation {gen[0]}/{s.generations}: best score "
                                         f"{intermediate_result.fun:,.0f}")
            return bool(cancel and cancel())

        res = differential_evolution(objective, list(zip(lo, hi)), init=init, maxiter=s.generations, seed=s.seed,
                                     mutation=s.mutation, recombination=s.recombination, polish=False,
                                     updating="deferred", callback=callback, tol=1e-6)
        finals = [archive.get(tuple(np.round(x, 6))) for x in np.atleast_2d(res.population)]
        finals = [c for c in finals if c is not None and c.evaluation.converged]
        best = archive.get(tuple(np.round(res.x, 6)))
        if best is not None:
            finals.insert(0, best)
        picked = _select_diverse(finals, s.keep, paths) if finals else []
        picked = [c for c in picked if c.config_key == key][: s.keep]
        for i, c in enumerate(picked):
            c.origin = f"evolved ({gen[0]} generations)"
            c.name = f"{members[0].name or 'AF'}-E{i + 1}"
        out += picked
    return sorted(out, key=lambda c: (not c.evaluation.feasible, c.evaluation.score))
