"""tests/test_gz_watchdog.py — решения вотчдога gz-sim (scripts/gz_watchdog.py)."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "gz_watchdog.py"
if not SCRIPT.exists():
    pytest.skip("scripts/gz_watchdog.py недоступен в этом окружении", allow_module_level=True)


@pytest.fixture(scope="module")
def wd():
    spec = importlib.util.spec_from_file_location("gz_watchdog_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_below_soft_threshold_does_nothing(wd):
    assert wd.decide(2000, airborne=False) == "none"
    assert wd.decide(2000, airborne=True) == "none"


def test_above_soft_restarts_only_when_everyone_is_on_the_ground(wd):
    assert wd.decide(5000, airborne=False) == "restart_idle"
    assert wd.decide(5000, airborne=True) == "wait"
    assert wd.decide(5000, airborne=None) == "wait"          # состояние неизвестно: не рискуем рейсом


def test_above_hard_restarts_regardless_of_flight(wd):
    assert wd.decide(7000, airborne=True) == "restart_now"
    assert wd.decide(7000, airborne=None) == "restart_now"


def test_unknown_rss_never_restarts(wd):
    assert wd.decide(None, airborne=False) == "none"


def test_custom_thresholds(wd):
    assert wd.decide(900, airborne=False, soft_mb=800, hard_mb=1200) == "restart_idle"
    assert wd.decide(1300, airborne=True, soft_mb=800, hard_mb=1200) == "restart_now"


def test_parse_gz_rss_picks_the_ruby_process(wd):
    out = "  RSS COMMAND\n 2048000 ruby\n   1500 python3\n 900000 ruby\n"
    assert wd.parse_gz_rss(out) == 2000
    assert wd.parse_gz_rss("  1500 python3\n") is None


POSE = '''
pose {
  name: "x500_swarm_1"
  position {
    x: 0
    y: 0
    z: %s
  }
}
pose {
  name: "x500_swarm_2"
  position {
    x: 5
    z: %s
  }
}
pose {
  name: "obst_1"
  position {
    z: 3
  }
}
'''


def test_parse_airborne(wd):
    assert wd.parse_airborne(POSE % ("0.13", "0.13")) is False
    assert wd.parse_airborne(POSE % ("0.13", "3.5")) is True
    assert wd.parse_airborne("nothing here") is None
