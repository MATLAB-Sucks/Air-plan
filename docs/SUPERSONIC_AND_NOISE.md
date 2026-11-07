# Supersonic flight, sonic boom and noise controls

## Supersonic design

Set **Cruise Mach** above 1.15 to get supersonic layouts:

* **Concorde-style slender delta:** tailless, with elevons, a single fin and
  engines under the wing.
* **X-59-style low-boom shape:** a very long ogive nose, a dorsal engine and a
  T-tail.

Speeds between Mach 0.90 and 1.15 are rejected, because they sit on the
transonic drag peak.

**What changes in the physics:**

* **Wave drag:** computed from the aircraft's actual cross-sectional area
  distribution (Whitcomb/von Kármán area rule, in Fourier form). It
  reproduces the analytic Sears–Haack drag to within 1%, and is corrected
  above Mach 1.2 with Raymer's Mach and sweep factor. One realism factor
  (1.5) covers oblique Mach-plane cuts, interference and spillage, which the
  normal-cut area rule misses.
* **Geometry:**
  * leading edges are swept behind the Mach cone (subsonic leading edges);
  * wings are 3% thick;
  * fuselages are slender, with ogive noses and an area-ruled waist ("coke
    bottle", the waist width derived from the wing's own cross-section);
  * cabins are at most 4 abreast, because wave drag grows with area squared.
* **Engines:** bypass ratio drives fuel burn, thrust lapse (ram compression
  helps low-bypass engines at Mach 2), nacelle size and jet noise. Reheat is
  credited for take-off and the transonic acceleration of low-bypass engines,
  as on Concorde.
* **Sizing:** supersonic designs are so sensitive (fuel is about half the
  take-off weight) that they are sized by a small constrained optimisation:
  SciPy Nelder–Mead over wing area, thrust and bypass ratio.
* **Other supersonic-specific rules:**
  * cruise-climb thrust is checked at mid-cruise weight;
  * landing weight is computed from the actual trip fuel;
  * slender deltas get vortex-lift CLmax and plate-like stiffness.

**Validation, with a Concorde-like mission** (12 t payload, 6,500 km,
Mach 2, 17 km, 3,600 m runway):

| | Aircraft Foundry | Concorde |
|---|---|---|
| Take-off mass | ≈ 201 t | 185 t |
| Cruise L/D | ≈ 6.6 | ≈ 7.3 |
| Sonic boom | ≈ 2 psf, ≈ 104 PLdB | ≈ 1.9–2 psf, ≈ 105 PLdB |

## Sonic boom (people on the ground)

The boom is predicted from each design's geometry with Whitham's far-field
theory:

* The equivalent area is the volume area plus the lift equivalent area,
  β/(2q)·∫L(x).
* It drives the F-function, which gives the N-wave overpressure, with ground
  reflection 1.9.

**Loudness** is calibrated to Concorde (2 psf ≈ 105 PLdB). It is then reduced
by a geometry-based shaping credit of up to 15 dB, which rewards:

* a long, slender nose;
* an engine shielded on top of the airframe;
* a gradual build-up of the area distribution.

These are the features NASA's X-59 uses to turn the boom into a "thump",
with a design target of about 75 PLdB. The shaping credit is an engineering
estimate, not a waveform-propagation calculation.

**Mach cutoff (truly boomless):** in a standard atmosphere the boom does not
reach the ground if the aircraft flies below about Mach 1.15 at cruise
altitude. The shock rays curve back upward first.

**Mission controls:**

* *Share of the route over land* and *Max sonic boom on the ground*.
* Over land the aircraft flies at its cruise Mach if its boom is within the
  limit. If not, it flies just below Mach cutoff, so it is still supersonic
  but boomless. If that is too inefficient, it flies at Mach 0.90.
* Fuel, trip time and the notes reflect the choice.

## Airport and cabin noise

* **Airport noise** (*Max airport noise*): jet-mixing noise at the 6.5 km
  take-off flyover point, using Lighthill's law (∝ T·Vj⁶). Calibrated to an
  A320 at about 85 dB, it puts Concorde-class engines about 35 dB higher.
  The design lever is bypass ratio: a slower jet is quieter. The trade-offs
  are bigger nacelles and worse supersonic efficiency.
* **Cabin noise** (*Max cabin noise for passengers*):
  * boundary-layer pressure (Lowson's Mach correction) plus engine noise at
    the cabin wall, minus mass-law transmission loss;
  * at supersonic speed the aircraft outruns its own jet noise;
  * the design lever is acoustic insulation, which adds weight. Quieter
    cabins are heavier, and that weight feeds back into the whole design.
