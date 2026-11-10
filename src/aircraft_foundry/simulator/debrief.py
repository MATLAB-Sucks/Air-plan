"""Crash and failure debriefs: what happened, the numbers, and how to stop it happening again -
both in how you fly and in the design."""
from __future__ import annotations

import math

G0 = 9.80665


def _kt(ms: float) -> float:
    return ms * 1.944


def debrief(event: dict, model, params, vref: float) -> dict:
    """{title, what, numbers[], flying[], design[]} for a crash / failure snapshot."""
    md, st = model, params.structure
    msg = event.get("message", "")
    v, g = event.get("airspeed", 0.0), event.get("load_factor", 1.0)
    vs = vref / 1.23
    out = {"title": msg, "what": "", "numbers": [], "flying": [], "design": []}
    if event.get("failure") in ("left wing", "right wing"):
        neg = g < 0
        lim = st.limit_load_factor if not neg else abs(getattr(st, "negative_load_factor", -1.0))
        ult = lim * st.safety_factor * st.wing_strength
        side = event["failure"].split()[0]
        lopsided = abs(event["stress"].get("left", 0) - event["stress"].get("right", 0)) > 0.15
        out.update(what=f"The {side} wing was loaded beyond its ultimate strength and broke.",
                   numbers=[f"Load factor {g:+.1f} g against a limit of {'-' if neg else '+'}{lim:.1f} g "
                            f"(breaks at {'-' if neg else '+'}{ult:.1f} g)", f"Speed {_kt(v):.0f} kt"],
                   flying=[f"Keep within {'-' if neg else '+'}{lim:.1f} g: the HUD shows g and OVERSTRESS before "
                           "anything breaks.",
                           f"Above manoeuvring speed (~{_kt(vs * math.sqrt(st.limit_load_factor)):.0f} kt) a full "
                           "pull can exceed the limit before the wing stalls - pull gently when fast."]
                   + (["You were rolling while pulling: a rolling pull-up loads one wing much more. Roll level "
                       "first, then pull."] if lopsided else []),
                   design=[f"Raise the {'negative' if neg else 'positive'} g limit (studio: Whole aircraft) or the "
                           "wing's strength reserve (studio: Main wing → Strength).",
                           "A stronger material (Al-Li, titanium, carbon) adds strength for less weight."])
    elif event.get("failure") == "tail":
        vd = math.sqrt(2 * md.q_dive / 1.225)
        out.update(what="The aircraft flew far beyond its design dive speed and the tail fluttered apart.",
                   numbers=[f"{_kt(v):.0f} kt true airspeed; design dive speed ≈ {_kt(vd):.0f} kt equivalent"],
                   flying=["Watch for OVERSPEED: close the throttle and raise the nose gently in a dive.",
                           "Never dive steeply at full power."],
                   design=["The dive speed follows the design cruise speed: a faster design gets a higher limit.",
                           "Tail strength reserve (studio: Tailplane / Fin → Strength) adds margin."])
    elif "sink rate" in msg:
        out.update(what="The landing was too hard for the landing gear.",
                   numbers=[f"Sink rate {event['sink_ms']:.1f} m/s ({event['sink_ms'] * 196.9:.0f} ft/min) - "
                            "gear limit ≈ 3 m/s", f"Speed {_kt(v):.0f} kt (VREF {_kt(vref):.0f} kt)"],
                   flying=["Fly the 3° glide path (PAPI two white, two red) at VREF.",
                           "Start the flare at ~30 ft: raise the nose gently and close the throttle; aim for "
                           "1–2 m/s (200–400 ft/min) at touchdown.",
                           "Try the autopilot's 'Land now' to watch a good approach."],
                   design=[f"A lower approach speed makes landing easier: a bigger wing or more flap "
                           f"(studio: Main wing → Flap span / chord) lowers VREF ({_kt(vref):.0f} kt now).",
                           "Stronger landing gear (studio: Landing gear → Strength) tolerates firmer landings."])
    elif "strike" in msg:
        out.update(what="Part of the airframe touched the ground: a wing tip or the tail.",
                   numbers=[f"Pitch {event.get('pitch', 0):+.0f}°, roll {event.get('roll', 0):+.0f}° at contact"],
                   flying=["Keep the wings level near the ground; in a crosswind use rudder, not bank.",
                           "Don't over-rotate: lift off at about 8–10° nose-up and flare only a few degrees."],
                   design=["Longer landing gear or more tail upsweep (studio: Fuselage) gives more clearance.",
                           "Wing dihedral and a high wing keep the tips further from the ground."])
    else:
        stalled = event.get("stalled")
        lowfuel = event.get("fuel_kg", 1) <= 0
        out.update(what=("The aircraft stalled and fell into the ground." if stalled else
                         "The engines stopped: the fuel ran out." if lowfuel else
                         "The aircraft flew into the ground or terrain."),
                   numbers=[f"Speed {_kt(v):.0f} kt (stall ≈ {_kt(vs):.0f} kt with landing flaps)",
                            f"Descending at {event['sink_ms'] * 196.9:.0f} ft/min, {g:+.1f} g"])
        if stalled:
            out["flying"] = [f"Keep above about {_kt(1.3 * vs):.0f} kt; the stick shakes / BUFFET warns of the stall.",
                             "To recover: stick forward to un-stall the wing, full power, wings level, then climb.",
                             "With rudder applied a stall becomes a spin: centre the rudder near the stall."]
            out["design"] = ["A lower stall speed (bigger wing, more flap) widens the safety margin.",
                             "A T-tail can lock into a deep stall - a conventional tail is more forgiving.",
                             "Wing twist (washout) makes the root stall first, keeping the ailerons working."]
        elif lowfuel:
            out["flying"] = ["Watch FUEL and ENDUR on the HUD; turn back with a reserve.",
                             "The autopilot's circuits can stop at a fuel reserve you choose."]
            out["design"] = ["More fuel capacity: a bigger wing holds more, or reduce cruise drag."]
        else:
            out["flying"] = ["Heed TERRAIN – PULL UP and SINK RATE warnings; keep at least 300 m above hills.",
                             "Plan routes around high ground; the autopilot keeps 300 m above terrain ahead."]
            out["design"] = ["A better climb rate (more thrust or less weight) clears terrain sooner."]
    return out


def debrief_html(d: dict) -> str:
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs)
    return (f"<b style='color:#e0533d'>{d['title']}</b><br>{d['what']}"
            + (f"<ul>{li(d['numbers'])}</ul>" if d["numbers"] else "")
            + (f"<b>Next time (flying)</b><ul>{li(d['flying'])}</ul>" if d["flying"] else "")
            + (f"<b>Fix the design</b><ul>{li(d['design'])}</ul>" if d["design"] else "")
            + "Press <b>R</b> to fly again.")
