"""Game controllers: shaping, flight mapping, UI mapping, and the simulator / main-window wiring."""
import os

import pytest

from aircraft_foundry.input.controller import (FakeBackend, PadSettings, PadState, flight_inputs, shape,
                                               ui_actions)
from aircraft_foundry.simulator.flightmodel import Controls


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def pad(axes=None, held=(), pressed=(), released=(), name="Xbox Wireless Controller"):
    a = {k: 0.0 for k in ("lx", "ly", "rx", "ry", "lt", "rt")}
    a.update(axes or {})
    return PadState(0, name, a, set(held), set(pressed), set(released))


def test_deadzone_and_expo():
    s = PadSettings(deadzone=0.1, expo=0.5)
    assert shape(0.08, s) == 0.0 and shape(1.0, s) == pytest.approx(1.0) and shape(-1.0, s) == pytest.approx(-1.0)
    assert 0 < shape(0.5, s) < 0.5  # softer around the centre


def test_flight_mapping():
    c = Controls()
    s = PadSettings(deadzone=0.0, expo=0.0)
    acts = flight_inputs(pad({"ly": 1.0, "lx": -0.5, "rt": 1.0, "ry": -1.0}, pressed={"south", "east"}), s, c, 0.5)
    assert c.elevator == pytest.approx(1.0)  # stick back = nose up
    assert c.aileron == pytest.approx(-0.5) and c.rudder > 0.9
    assert c.throttle == pytest.approx(0.3)  # right stick up raises the throttle
    assert {"flaps", "gear", "manual"} <= acts
    flight_inputs(pad({"ly": 1.0}), PadSettings(0.0, 0.0, invert_pitch=True), c, 0.1)
    assert c.elevator == pytest.approx(-1.0)
    flight_inputs(pad(pressed={"west"}, held={"west"}), s, c, 0.1)
    assert c.brakes
    flight_inputs(pad(released={"west"}), s, c, 0.1)
    assert not c.brakes
    t0 = c.pitch_trim
    flight_inputs(pad(held={"rb"}), s, c, 1.0)
    assert c.pitch_trim > t0


def test_controller_families_recognised():
    for name, kind in (("Nintendo Switch Pro Controller", "Switch"), ("Joy-Con (L/R)", "Joy-Con"),
                       ("PS5 Controller", "PlayStation"), ("DualSense Wireless Controller", "PlayStation"),
                       ("Xbox Series X Controller", "Xbox")):
        assert pad(name=name).kind() == kind


def test_ui_mapping_with_repeat():
    rep = {}
    s = PadSettings()
    a = ui_actions(pad(held={"down"}, pressed={"south", "rb"}), s, rep, 0.0)
    assert a["keys"] == ["down", "activate"] and a["page"] == 1
    assert ui_actions(pad(held={"down"}), s, rep, 0.2)["keys"] == []  # no repeat before the delay
    later = [k for t in (0.5, 0.52, 0.56, 0.6, 0.64) for k in ui_actions(pad(held={"down"}), s, rep, t)["keys"]]
    assert later.count("down") >= 1  # auto-repeat after holding
    assert ui_actions(pad({"rx": 0.9, "rt": 1.0}), s, rep, 1.0)["orbit"][0] > 0.5


def test_manager_with_fake_backend_and_rumble(app):
    from aircraft_foundry.app.controller_page import ControllerManager, ControllerPage
    fb = FakeBackend()
    m = ControllerManager(fb)
    page = ControllerPage(m)
    names = []
    m.pads_changed.connect(names.append)
    fb.states = [pad(name="DualSense Wireless Controller")]
    m.poll()
    assert names and "PlayStation" in names[-1][0] and "Connected" in page.status.text()
    m.rumble(0.5, 0.5, 100)
    assert fb.rumbles


@pytest.mark.skipif(not os.environ.get("AF_GUI_TESTS"), reason="needs OpenGL (set AF_GUI_TESTS=1)")
def test_simulator_flies_with_a_controller(app):
    from aircraft_foundry.app.controller_page import ControllerManager
    from aircraft_foundry.core.design import AircraftDesign
    from aircraft_foundry.core.evaluator import evaluate
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    from aircraft_foundry.simulator.window import SimulatorWindow
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    w = SimulatorWindow(AircraftDesign(p, "pad"), m, evaluate(p, m))
    w.timer.stop()
    fb = FakeBackend()
    w.pads = ControllerManager(fb)
    fb.states = [pad({"ry": -1.0}, pressed={"west"}, held={"west"})]  # throttle up, brakes held
    w.pads.poll()
    w.sim.controls.brakes = False
    w._update_controls(1.0)
    assert w.sim.controls.throttle > 0.4 and w.sim.controls.brakes
    fb.states = [pad(released={"west"}, pressed={"south"})]
    w.pads.poll()
    flaps0 = w.sim.controls.flaps
    w._update_controls(0.05)
    assert not w.sim.controls.brakes and w.sim.controls.flaps != flaps0  # flaps cycled
    w.restart("cruise")
    w.ap_program.setCurrentIndex(0)
    w.engage_autopilot()
    assert w.ap.engaged
    fb.states = [pad({"lx": 0.9})]
    w.pads.poll()
    w._update_controls(0.05)
    assert not w.ap.engaged  # stick input takes over from the autopilot
    w.sim.crashed = "test crash"
    w._rumble_events()
    assert fb.rumbles and fb.rumbles[-1][1] == 1.0  # strong rumble on losing the aircraft
    w.close()
