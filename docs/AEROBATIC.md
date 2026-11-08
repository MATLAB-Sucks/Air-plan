# Stunt planes (aerobatic category)

Choose **Aircraft category → aerobatic** in the mission panel, or press
**Stunt plane preset**. In the design studio, press **Stunt plane
(aerobatic)**. Either way the app designs an unlimited-category aerobatic
aircraft in the class of the Extra 330 or Sukhoi Su-26.

| | Aircraft Foundry stunt plane | Extra 330LX |
|---|---|---|
| Take-off mass | ≈ 1,050 kg | 950 kg |
| Span / length | ≈ 8.2 m / 7.2 m | 8.0 m / 7.1 m |
| g rating | ±10 g (ultimate ±15 g) | ±10 g |
| Roll rate | ≈ 475°/s at manoeuvring speed | ≈ 400°/s |
| Static thrust / weight | 0.6 | about 0.6 |

**What changes for a stunt plane**

* **Structure follows the g rating.** Every weight equation now uses the
  design's own ultimate load factor (limit × safety factor) instead of a
  fixed airliner value, so a ±10 g airframe really is heavier. In the flight
  simulator the negative-g limit is symmetric (−10 g) instead of an
  airliner's −1 g.
* **Light-aircraft weight statistics.** Light designs (the aerobatic
  category, and small propeller aircraft) use Raymer's general-aviation
  equations for the wing, tails, fuselage, gear, fuel system, controls,
  avionics and electrics. The transport equations overestimate a 1-tonne
  aircraft three to four times.
* **Geometry:**
  * symmetric aerofoil, so it flies the same upside down;
  * near-full-span ailerons with 30% chord, and no flaps;
  * large tails (horizontal about 30%, vertical about 14% of the wing area)
    with 35%-chord elevator and rudder, the largest the tail spars allow;
  * a single tractor propeller in the nose (new engine position *nose*);
  * a tandem cockpit.
* **Requirements:**
  * roll rate of at least 240°/s at manoeuvring speed (strip theory);
  * static thrust of at least 45% of weight;
  * a symmetric aerofoil;
  * a g rating of at least 6 g.
* **Wider parameter ranges.** The limit load factor goes up to 12 g, the
  ailerons can start at 10% of the semi-span with chords up to 50%, the
  elevator and rudder can have chords up to 60%, and flaps are optional.
  Tail control surfaces deeper than 35% chord are flagged by the
  hinge-clearance check, because the hinge would sit ahead of the tail's
  rear spar.

**In the simulator:**

* the HUD shows the live roll rate and the flight's peak positive and
  negative g against the limits;
* OVERSTRESS appears beyond the limit load;
* the wing breaks beyond the ultimate load, in either direction.

The simulated roll rate agrees with the evaluator's strip theory (about
335°/s at 146 kt for the preset design).

**Limits.** Propeller engines use the turboprop model, so there is no
piston-engine power lapse. Spins, snap rolls and gyroscopic or propeller
torque effects are not modelled.
