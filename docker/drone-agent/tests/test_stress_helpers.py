"""tests/test_stress_helpers.py — помощники scripts/stress_test.py (ложный успех Вектора 1 при неприменённом хаосе)."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "stress_test.py"
if not SCRIPT.exists():                                  # в контейнере drone-agent репозитория нет
    pytest.skip("scripts/stress_test.py недоступен в этом окружении", allow_module_level=True)


@pytest.fixture(scope="module")
def st():
    spec = importlib.util.spec_from_file_location("stress_test_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reply(obj, rc=0):
    return subprocess.CompletedProcess([], rc, stdout=json.dumps(obj) if not isinstance(obj, str) else obj, stderr="")


def test_all_agents_ok_gives_no_errors(st):
    assert st.ew_agent_errors(reply({"1": {"ok": True, "status": 200, "body": {}}, "2": {"ok": True}})) == []


def test_http_200_with_agent_failure_is_reported(st):
    """Регрессия: curl -f и ewctl.sh видят только HTTP 200, а агенты ответили 500 внутри тела."""
    errs = st.ew_agent_errors(reply({"1": {"ok": False, "status": 500,
                                          "body": {"error": "tc qdisc add ... Specified qdisc kind is unknown."}},
                                    "2": {"ok": True}}))
    assert len(errs) == 1 and errs[0].startswith("D1:") and "qdisc kind is unknown" in errs[0]


def test_every_failing_agent_is_listed(st):
    errs = st.ew_agent_errors(reply({str(i): {"ok": False, "status": 500, "body": {"error": "x"}} for i in (1, 2, 3)}))
    assert [e[:2] for e in errs] == ["D1", "D2", "D3"]


def test_non_json_output_counts_as_an_error(st):
    errs = st.ew_agent_errors(reply("curl: (7) Failed to connect"))
    assert len(errs) == 1 and "не JSON" in errs[0]


def test_empty_output_is_not_an_error(st):
    assert st.ew_agent_errors(reply("")) == []
