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
