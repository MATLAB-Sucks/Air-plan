import pytest

from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.parameters import AircraftParameters


@pytest.fixture
def mission():
    return MissionRequirements()


@pytest.fixture
def params():
    return AircraftParameters()


@pytest.fixture(scope="session")
def baseline_params():
    from aircraft_foundry.core.parameters import ConfigurationParams
    from aircraft_foundry.core.synthesis import baseline
    return baseline(MissionRequirements(), ConfigurationParams())


# GUI runs: after the summary is printed, end the process at once - tearing down leftover Qt / VTK
# windows at interpreter exit can segfault (the app avoids the same native teardown via hard_exit).
_EXIT = {"status": 0}


def pytest_sessionfinish(session, exitstatus):
    _EXIT["status"] = int(exitstatus)


def pytest_unconfigure(config):
    import os
    import sys
    if os.environ.get("AF_GUI_TESTS"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_EXIT["status"])
