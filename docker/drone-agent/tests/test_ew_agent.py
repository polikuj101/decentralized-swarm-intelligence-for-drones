"""tests/test_ew_agent.py — nftables-эмуляция деградации mesh (ядра без netem/prio, например WSL2) и логика отказа.
Запускается и на хосте (pip install pytest fastapi pydantic), и в drone-agent (make test-s1)."""
import importlib.util
import ipaddress
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")

EW = Path(__file__).resolve().parents[1] / "ew" / "ew_agent.py"
if not EW.exists():                                      # в контейнере агент лежит в /opt/ew
    EW = Path("/opt/ew/ew_agent.py")
if not EW.exists():
    pytest.skip("ew_agent.py недоступен", allow_module_level=True)

IP_JSON = json.dumps([
    {"ifname": "eth0", "addr_info": [{"local": "172.30.0.21"}]},
    {"ifname": "eth1", "addr_info": [{"local": "172.32.0.11"}]},
    {"ifname": "eth2", "addr_info": [{"local": "172.31.0.11"}]},
])


@pytest.fixture(scope="module")
def ew():
    """Импорт ew_agent с подменой `ip` (модуль определяет интерфейсы при импорте) и pymavlink, если его нет."""
    os.environ["DRONE_ID"] = "1"
    os.environ["SWARM_SIZE"] = "5"
    try:
        import pymavlink  # noqa: F401
    except ImportError:
        pm = types.ModuleType("pymavlink")
        pm.mavutil = types.SimpleNamespace()
        sys.modules["pymavlink"] = pm
    real_run = subprocess.run
    subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=IP_JSON, stderr="")
    try:
        spec = importlib.util.spec_from_file_location("ew_agent_under_test", EW)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        subprocess.run = real_run
    return mod


def prof(ew, **kw):
    return ew.MeshProfile(**kw)


def test_hard_split_drops_both_directions_for_peers_only(ew):
    script, unsupported = ew.nft_mesh_script("eth2", prof(ew, loss_pct=100, peers=[4, 5]), ["172.31.0.14", "172.31.0.15"])
    assert unsupported == []
    assert 'oifname "eth2" ip daddr { 172.31.0.14, 172.31.0.15 } counter drop' in script
    assert 'iifname "eth2" ip saddr { 172.31.0.14, 172.31.0.15 } counter drop' in script
    assert script.startswith("table inet ew_mesh\ndelete table inet ew_mesh\n")


def test_full_blackout_without_peers_drops_whole_interface(ew):
    script, _ = ew.nft_mesh_script("eth2", prof(ew, loss_pct=100), None)
    assert 'oifname "eth2" counter drop' in script and "iifname" not in script


def test_partial_loss_uses_random_drop_not_a_full_drop(ew):
    """Регрессия: при loss<90 без пиров fallback раньше молча ничего не применял, а при пирах резал 100%."""
    script, _ = ew.nft_mesh_script("eth2", prof(ew, loss_pct=50), None)
    assert 'iifname "eth2" numgen random mod 1000 < 500 counter drop' in script
    peers, _ = ew.nft_mesh_script("eth2", prof(ew, loss_pct=30, peers=[4]), ["172.31.0.14"])
    assert "ip saddr { 172.31.0.14 } numgen random mod 1000 < 300 counter drop" in peers
    # drop в хуке output даёт отправителю ошибку sendto и рвёт линк Zenoh (опыт 2026-10-28): режем только на приёме
    assert "oifname" not in script and "oifname" not in peers


def test_fractional_loss_has_permille_resolution(ew):
    script, _ = ew.nft_mesh_script("eth2", prof(ew, loss_pct=12.5), None)
    assert "mod 1000 < 125 " in script


def test_rate_limit_is_converted_from_kbit_to_bytes_per_second(ew):
    script, _ = ew.nft_mesh_script("eth2", prof(ew, rate_kbit=64), None)
    assert "limit rate over 8000 bytes/second counter drop" in script


def test_unsupported_features_are_reported_not_hidden(ew):
    _, unsupported = ew.nft_mesh_script("eth2", prof(ew, loss_pct=30, loss_corr_pct=25, delay_ms=50, jitter_ms=50, rate_kbit=64), None)
    assert any("delay" in u for u in unsupported) and any("loss_corr" in u for u in unsupported)


def test_profile_that_cannot_be_emulated_gives_no_script(ew):
    assert ew.nft_mesh_script("eth2", prof(ew, delay_ms=200), None)[0] is None
    assert ew.nft_mesh_script("eth2", prof(ew), None)[0] is None


# ───────────── mesh_apply: поведение при ядре без netem ─────────────

class FakeSh:
    def __init__(self, ew, tc_fails=True):
        self.ew, self.calls, self.tc_fails = ew, [], tc_fails

    def __call__(self, cmd, check=True, stdin=None):
        self.calls.append((cmd, stdin))
        if cmd[0] == "tc" and cmd[2] in ("add",) and self.tc_fails:
            raise self.ew.EwError("tc add -> rc=2: Specified qdisc kind is unknown.")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture()
def patched(ew, monkeypatch):
    fake = FakeSh(ew)
    monkeypatch.setattr(ew, "sh", fake)
    ew.state["mesh"] = {"active": False, "profile": None, "since": None}
    return ew, fake


def test_apply_falls_back_to_nftables_and_records_emulation(patched):
    ew, fake = patched
    ew.mesh_apply(ew.MeshProfile(loss_pct=100, peers=[4, 5]))
    nft = [c for c in fake.calls if c[0][:2] == ["nft", "-f"]]
    assert len(nft) == 1 and "ip daddr { 172.31.0.14, 172.31.0.15 }" in nft[0][1]
    prof_state = ew.state["mesh"]["profile"]
    assert ew.state["mesh"]["active"] and prof_state["emulation"] == "nftables" and prof_state["unsupported"] == []


def test_apply_reports_unsupported_features_in_state(patched):
    ew, _ = patched
    ew.mesh_apply(ew.MeshProfile(loss_pct=30, loss_corr_pct=25, delay_ms=50, jitter_ms=50, rate_kbit=64, limit_pkts=100))
    assert ew.state["mesh"]["profile"]["emulation"] == "nftables"
    assert "delay/jitter" in ew.state["mesh"]["profile"]["unsupported"]


def test_apply_refuses_instead_of_pretending_when_nothing_can_be_emulated(patched):
    """Регрессия: delay-only профиль на ядре без netem раньше давал active=True без какого-либо эффекта."""
    ew, _ = patched
    with pytest.raises(ew.EwError, match="нельзя эмулировать"):
        ew.mesh_apply(ew.MeshProfile(delay_ms=200))
    assert ew.state["mesh"]["active"] is False


def test_apply_rejects_invalid_peer_before_touching_anything(patched):
    ew, fake = patched
    with pytest.raises(ew.EwError, match="invalid peer"):
        ew.mesh_apply(ew.MeshProfile(loss_pct=100, peers=[1]))          # сам себе
    with pytest.raises(ew.EwError, match="invalid peer"):
        ew.mesh_apply(ew.MeshProfile(loss_pct=100, peers=[9]))
    assert not any(c[0][:2] == ["nft", "-f"] for c in fake.calls)


def test_apply_uses_tc_when_available(ew, monkeypatch):
    fake = FakeSh(ew, tc_fails=False)
    monkeypatch.setattr(ew, "sh", fake)
    ew.mesh_apply(ew.MeshProfile(loss_pct=50))
    assert ew.state["mesh"]["profile"]["emulation"] == "tc"
    assert not any(c[0][:2] == ["nft", "-f"] for c in fake.calls)


def test_peer_ip_layout_matches_mesh_net(ew):
    assert ew.peer_ip(4) == str(ipaddress.ip_network("172.31.0.0/24").network_address + 14)


# ───────────── Bearer: пустой EW_TOKEN = отказ (fail-closed) ─────────────

def test_agent_auth_fails_closed_without_token(ew, monkeypatch):
    """Регрессия: `if EW_TOKEN and ...` при пустом токене пропускал любые команды РЭБ."""
    from fastapi import HTTPException
    monkeypatch.setattr(ew, "EW_TOKEN", "")
    for header in ("", "Bearer "):
        with pytest.raises(HTTPException) as e:
            ew.auth(header)
        assert e.value.status_code == 503


def test_agent_auth_rejects_wrong_and_accepts_right_token(ew, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(ew, "EW_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as e:
        ew.auth("Bearer nope")
    assert e.value.status_code == 401
    assert ew.auth("Bearer s3cret") is None


def test_agent_auth_handles_non_ascii_header_without_crashing(ew, monkeypatch):
    """hmac.compare_digest над str падает на не-ASCII; сравнение идёт по байтам, поэтому это должен быть 401, а не 500."""
    from fastapi import HTTPException
    monkeypatch.setattr(ew, "EW_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as e:
        ew.auth("Bearer токен")
    assert e.value.status_code == 401
