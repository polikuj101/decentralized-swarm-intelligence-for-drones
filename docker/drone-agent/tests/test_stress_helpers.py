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


def test_nonzero_exit_is_an_error_even_with_empty_output(st):
    """Регрессия: curl -f при недоступном ew-controller возвращает код != 0 и пустой stdout — раньше это выглядело как «ошибок нет»."""
    errs = st.ew_agent_errors(reply("", rc=22))
    assert len(errs) == 1 and "22" in errs[0]


# -- Векторы 2-4: инъекция должна быть подтверждена наблюдаемым эффектом --

def test_survivors_see_dead_requires_every_survivor_to_mark_them(st):
    states = {1: {}, 2: {}, 3: {"alive": [3, 4, 5]}, 4: {"alive": [3, 4, 5]}, 5: {"alive": [3, 4, 5]}}
    assert st.survivors_see_dead(states, [1, 2]) is True
    states[4] = {"alive": [2, 3, 4, 5]}                       # борт 4 всё ещё считает убитого 2 живым
    assert st.survivors_see_dead(states, [1, 2]) is False


def test_survivors_see_dead_empty_state_is_not_evidence(st):
    """Регрессия: ответ {} раньше выглядел как «никто не завис»; молчащий выживший = нет подтверждения."""
    assert st.survivors_see_dead({1: {}, 2: {"alive": [2, 3]}, 3: {}}, [1]) is False
    assert st.survivors_see_dead({1: {}}, [1]) is False       # выживших нет совсем


def test_gps_denial_visible_by_src_change_or_suspect(st):
    assert st.gps_denial_visible("GPS", {"GPS", "DR"}, False) is True
    assert st.gps_denial_visible("GPS", {"GPS"}, True) is True
    assert st.gps_denial_visible("GPS", {"GPS"}, False) is False       # GPS не отключали
    assert st.gps_denial_visible("DR", {"DR"}, False) is False         # уже был DR до инъекции


def test_cpu_limit_applied_matches_nanocpus(st):
    assert st.cpu_limit_applied("500000000", 0.5) is True
    assert st.cpu_limit_applied(4_000_000_000, 0.5) is False
    assert st.cpu_limit_applied("", 0.5) is False
    assert st.cpu_limit_applied(None, 0.5) is False


def test_rss_readings_valid_rejects_missing_process(st):
    assert st.rss_readings_valid({1: 120000, 2: 130000}, {1: 125000, 2: 131000}) is True
    assert st.rss_readings_valid({1: 0, 2: 130000}, {1: 125000, 2: 131000}) is False


# -- Вердикты, которые раньше проходили вхолостую

def test_reassignment_not_happening_is_a_failure(st):
    """Регрессия: reassigned_at is None (задачу так и не переназначили) раньше считалось успехом."""
    assert not st.reassignment_ok(task_before=8, declared_at=3.0, reassigned_at=None)
    assert not st.reassignment_ok(task_before=8, declared_at=None, reassigned_at=None)


def test_reassignment_within_limit_passes_and_slow_fails(st):
    assert st.reassignment_ok(8, 3.0, 8.0)
    assert not st.reassignment_ok(8, 3.0, 9.5)


def test_reassignment_without_victim_task_has_nothing_to_check(st):
    assert st.reassignment_ok(None, None, None)


def test_agreed_winner_needs_every_board_and_one_value(st):
    assert st.agreed_winner({1: 2, 2: 2, 3: 2}, 3) == 2
    assert st.agreed_winner({1: 2, 2: 3, 3: 2}, 3) is None
    assert st.agreed_winner({1: 2, 2: None, 3: 2}, 3) is None
    assert st.agreed_winner({1: 2, 2: 2}, 3) is None


def test_swarm_settled_requires_full_alive_and_single_epoch(st):
    ok = {i: {"alive": [1, 2, 3], "epoch": 4} for i in (1, 2, 3)}
    assert st.swarm_settled(ok, 3)
    ok[2] = {"alive": [1, 2], "epoch": 4}
    assert not st.swarm_settled(ok, 3)
    split_epoch = {i: {"alive": [1, 2, 3], "epoch": 4 + (i == 3)} for i in (1, 2, 3)}
    assert not st.swarm_settled(split_epoch, 3)
    assert not st.swarm_settled({1: {}, 2: {}, 3: {}}, 3)


def _t(*vals):
    return {"per_board": {i + 1: v for i, v in enumerate(vals)}, "agreed": vals[0] if len(set(vals)) == 1 else None}


def test_collision_resolved_when_boards_agree_then_task_finishes(st):
    tl = [_t(1, None, None)] + [_t(1, 1, 1)] * 4 + [_t(None, None, None)] * 3
    assert st.collision_resolved(tl, 3)


def test_collision_not_resolved_when_boards_disagree_after_agreement(st):
    tl = [_t(1, 1, 1)] * 3 + [_t(1, 2, 1)]
    assert not st.collision_resolved(tl, 3)


def test_collision_not_resolved_when_only_some_boards_lose_the_task(st):
    tl = [_t(1, 1, 1)] * 3 + [_t(1, None, 1)]
    assert not st.collision_resolved(tl, 3)


def test_collision_not_resolved_without_agreement_or_with_too_short_agreement(st):
    assert not st.collision_resolved([_t(None, None, None)] * 5, 3)
    assert not st.collision_resolved([_t(1, 1, 1)] * 2 + [_t(None, None, None)], 3)


def test_collision_not_resolved_if_task_reappears_after_finishing(st):
    assert not st.collision_resolved([_t(1, 1, 1)] * 3 + [_t(None, None, None), _t(1, 1, 1)], 3)
