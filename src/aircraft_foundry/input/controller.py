"""Game controllers (Bluetooth or USB): Xbox, PlayStation (DualShock 4 / DualSense) and Nintendo Switch
(Pro Controller, Joy-Con pairs or a single Joy-Con held sideways), plus most other pads SDL knows.

Uses SDL2's game-controller layer through pygame, which maps every supported pad onto one standard
layout.  Buttons are read by **position**, not by the letter printed on them, so "south" is the bottom
face button everywhere (Xbox A, PlayStation Cross, Switch B).

Pair the controller with the computer first (Bluetooth settings); it then appears automatically and can
be connected or disconnected while the app runs.  On macOS some controllers need the app to be allowed
under System Settings > Privacy & Security > Input Monitoring.

The mapping functions are pure (state in, actions out) so they are unit-tested without hardware.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

AXES = ("lx", "ly", "rx", "ry", "lt", "rt")
BUTTONS = ("south", "east", "west", "north", "back", "guide", "start", "ls", "rs", "lb", "rb",
           "up", "down", "left", "right")
# SDL game-controller enum order (CONTROLLER_BUTTON_A .. DPAD_RIGHT) -> positional names
_SDL_BUTTON_ORDER = ("south", "east", "west", "north", "back", "guide", "start", "ls", "rs", "lb", "rb",
                     "up", "down", "left", "right")

SDL_HINTS = {
    "SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS": "1",  # the app's window is Qt's, not SDL's: read pads anyway
    "SDL_JOYSTICK_HIDAPI": "1",
    "SDL_JOYSTICK_HIDAPI_SWITCH": "1",  # Switch Pro Controller
    "SDL_JOYSTICK_HIDAPI_JOY_CONS": "1",  # Joy-Cons
    "SDL_JOYSTICK_HIDAPI_COMBINE_JOY_CONS": "1",  # a left + right Joy-Con act as one controller
    "SDL_JOYSTICK_HIDAPI_PS4": "1",
    "SDL_JOYSTICK_HIDAPI_PS5": "1",  # DualSense
    "SDL_JOYSTICK_HIDAPI_PS4_RUMBLE": "1",
    "SDL_JOYSTICK_HIDAPI_PS5_RUMBLE": "1",
    "SDL_JOYSTICK_HIDAPI_XBOX": "1",
    "SDL_GAMECONTROLLER_USE_BUTTON_LABELS": "0",  # positional layout (Nintendo A/B swapped like Xbox)
    "SDL_VIDEODRIVER": "dummy",  # SDL needs a video driver to pump events; Qt owns the real windows
    "SDL_AUDIODRIVER": "dummy",
    "PYGAME_HIDE_SUPPORT_PROMPT": "1",
}


@dataclass
class PadState:
    index: int
    name: str
    axes: dict = field(default_factory=lambda: {a: 0.0 for a in AXES})
    held: set = field(default_factory=set)
    pressed: set = field(default_factory=set)  # went down since the previous poll
    released: set = field(default_factory=set)

    def kind(self) -> str:
        n = self.name.lower()
        if "joy-con" in n or "joycon" in n:
            return "Joy-Con"
        if "pro controller" in n or "switch" in n or "nintendo" in n:
            return "Switch"
        if "dualsense" in n or "ps5" in n or "dualshock" in n or "ps4" in n or "playstation" in n:
            return "PlayStation"
        if "xbox" in n or "xinput" in n or "microsoft" in n:
            return "Xbox"
        return "Controller"


@dataclass
class PadSettings:
    deadzone: float = 0.12
    expo: float = 0.35  # 0 = linear, 1 = very soft around the centre
    invert_pitch: bool = False
    sensitivity: float = 1.0
    rumble: bool = True


def shape(v: float, s: PadSettings) -> float:
    """Dead zone, then exponential curve (fine control near centre, full deflection at the edge)."""
    a = abs(v)
    if a <= s.deadzone:
        return 0.0
    a = min((a - s.deadzone) / (1.0 - s.deadzone), 1.0)
    a = (1 - s.expo) * a + s.expo * a ** 3
    return math.copysign(min(a * s.sensitivity, 1.0), v)


# ----------------------------------------------------------------------------- flight mapping
FLIGHT_HELP = [
    ("Left stick", "Pitch (pull back = nose up) and roll"),
    ("LT / RT  (ZL / ZR, L2 / R2)", "Rudder left / right (steers on the ground)"),
    ("Right stick up / down", "Throttle up / down"),
    ("D-pad up / down", "Throttle steps of 10 %"),
    ("LB / RB  (L / R, L1 / R1)", "Pitch trim down / up (hold)"),
    ("South button  (A / Cross / Switch B)", "Flaps: up → take-off → landing"),
    ("East button  (B / Circle / Switch A)", "Landing gear up / down"),
    ("West button  (X / Square / Switch Y)", "Brakes (while held)"),
    ("North button  (Y / Triangle / Switch X)", "Camera view"),
    ("Left-stick click", "Stability augmentation on / off"),
    ("Start / Menu / Options / +", "Pause"),
    ("Back / View / Share / −", "Restart on the runway"),
]


def flight_inputs(state: PadState, s: PadSettings, controls, dt: float) -> set[str]:
    """Apply a controller to the simulator's Controls; returns one-shot actions for the window."""
    ax = state.axes
    pitch = shape(ax["ly"], s)  # SDL: stick pulled back/down is positive -> nose up
    controls.elevator = -pitch if s.invert_pitch else pitch
    controls.aileron = shape(ax["lx"], s)
    controls.rudder = max(-1.0, min(1.0, shape(ax["rt"], PadSettings(0.05, 0.0)) - shape(ax["lt"], PadSettings(0.05, 0.0))))
    thr = shape(-ax["ry"], PadSettings(0.2, 0.0))
    controls.throttle = max(0.0, min(1.0, controls.throttle + 0.6 * thr * dt))
    if "up" in state.pressed:
        controls.throttle = min(1.0, round(controls.throttle + 0.1, 2))
    if "down" in state.pressed:
        controls.throttle = max(0.0, round(controls.throttle - 0.1, 2))
    if "lb" in state.held:
        controls.pitch_trim = max(-1.0, controls.pitch_trim - 0.25 * dt)
    if "rb" in state.held:
        controls.pitch_trim = min(1.0, controls.pitch_trim + 0.25 * dt)
    if "west" in state.pressed:  # brakes while the button is held (the keyboard's B toggle still works)
        controls.brakes = True
    if "west" in state.released:
        controls.brakes = False
    actions = set()
    for btn, action in (("south", "flaps"), ("east", "gear"), ("north", "camera"), ("ls", "sas"),
                        ("start", "pause"), ("back", "restart")):
        if btn in state.pressed:
            actions.add(action)
    if any(abs(v) > 0.5 for k, v in ax.items() if k in ("lx", "ly")):
        actions.add("manual")  # stick input takes over from the autopilot
    return actions


# ----------------------------------------------------------------------------- main-window mapping
UI_HELP = [
    ("D-pad / left stick", "Move between controls (up/down) and adjust them (left/right)"),
    ("South button  (A / Cross / Switch B)", "Press / tick the focused control"),
    ("East button  (B / Circle / Switch A)", "Back / close a dialog"),
    ("LB / RB", "Previous / next page in the workspace"),
    ("Right stick", "Orbit the 3-D view"),
    ("LT / RT", "Zoom the 3-D view out / in"),
    ("Start", "Fly the selected aircraft"),
]


def ui_actions(state: PadState, s: PadSettings, repeat: dict, now: float) -> dict:
    """Navigation intents for the main window (with key-repeat on held directions)."""
    out = {"keys": [], "page": 0, "orbit": (0.0, 0.0), "zoom": 0.0, "fly": False}
    dirs = {"up": "ly<", "down": "ly>", "left": "lx<", "right": "lx>"}
    for d, stick in dirs.items():
        v = state.axes[stick[:2]]
        active = d in state.held or (v < -0.6 if stick[2] == "<" else v > 0.6)
        if active:
            first = repeat.get(d)
            if first is None:
                repeat[d] = now
                out["keys"].append(d)
            elif now - first > 0.45 and int((now - first - 0.45) / 0.09) != int((now - first - 0.45 - 1 / 60) / 0.09):
                out["keys"].append(d)
        else:
            repeat.pop(d, None)
    if "south" in state.pressed:
        out["keys"].append("activate")
    if "east" in state.pressed:
        out["keys"].append("back")
    out["page"] = (1 if "rb" in state.pressed else 0) - (1 if "lb" in state.pressed else 0)
    out["orbit"] = (shape(state.axes["rx"], s), shape(state.axes["ry"], s))
    out["zoom"] = state.axes["rt"] - state.axes["lt"]
    out["fly"] = "start" in state.pressed
    return out


# ----------------------------------------------------------------------------- backends
class NullBackend:
    available = False
    error = "Controller support needs the 'pygame' package (SDL2)."

    def poll(self) -> list[PadState]:
        return []

    def rumble(self, index: int, low: float, high: float, ms: int) -> None:
        pass


class FakeBackend:
    """For tests and demos: feed states directly."""
    available = True
    error = ""

    def __init__(self):
        self.states: list[PadState] = []
        self.rumbles: list[tuple] = []

    def poll(self) -> list[PadState]:
        return self.states

    def rumble(self, index, low, high, ms):
        self.rumbles.append((index, low, high, ms))


class SDLBackend:
    """SDL2 game controllers through pygame (hot-plug aware)."""

    def __init__(self):
        for k, v in SDL_HINTS.items():
            os.environ.setdefault(k, v)
        self.available, self.error = False, ""
        try:
            import pygame
            from pygame._sdl2 import controller
            pygame.display.init()  # dummy video driver: needed for SDL's event pump only
            pygame.joystick.init()
            controller.init()
        except Exception as exc:  # pygame missing or SDL failed: the app works without controllers
            self.error = f"Controllers unavailable: {exc}"
            return
        self.pg, self.ctl = pygame, controller
        self.pads: dict[int, object] = {}
        self.prev: dict[int, set] = {}
        self.available = True
        self._scan()

    def _scan(self):
        for i in range(self.ctl.get_count()):
            if i not in self.pads and self.ctl.is_controller(i):
                try:
                    self.pads[i] = self.ctl.Controller(i)
                except Exception:
                    pass

    def poll(self) -> list[PadState]:
        if not self.available:
            return []
        rescan = False
        for ev in self.pg.event.get():
            if ev.type in (self.pg.CONTROLLERDEVICEADDED, self.pg.CONTROLLERDEVICEREMOVED,
                           getattr(self.pg, "JOYDEVICEADDED", -1), getattr(self.pg, "JOYDEVICEREMOVED", -1)):
                rescan = True
        if rescan:
            for i, pad in list(self.pads.items()):
                if not pad.attached():
                    del self.pads[i]
            self._scan()
        out = []
        for i, pad in self.pads.items():
            try:
                raw = [pad.get_axis(k) / 32767.0 for k in range(6)]
                held = {name for k, name in enumerate(_SDL_BUTTON_ORDER) if pad.get_button(k)}
            except Exception:
                continue
            prev = self.prev.get(i, set())
            st = PadState(i, pad.name or "Controller",
                          {"lx": raw[0], "ly": raw[1], "rx": raw[2], "ry": raw[3],
                           "lt": max(raw[4], 0.0), "rt": max(raw[5], 0.0)},
                          held, held - prev, prev - held)
            self.prev[i] = held
            out.append(st)
        return out

    def rumble(self, index: int, low: float, high: float, ms: int) -> None:
        pad = self.pads.get(index)
        if pad is not None:
            try:
                pad.rumble(max(0.0, min(low, 1.0)), max(0.0, min(high, 1.0)), int(ms))
            except Exception:
                pass


def make_backend():
    try:
        import pygame  # noqa: F401
    except Exception:
        return NullBackend()
    b = SDLBackend()
    return b if b.available else NullBackend()
