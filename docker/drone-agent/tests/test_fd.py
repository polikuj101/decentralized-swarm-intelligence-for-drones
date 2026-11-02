"""tests/test_fd.py — журнал детектора отказа соседа и офлайн-проверка детекторов (docs/PLAN_LEARNED_FAILURE_DETECTOR.md)."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.fd import B0, B1, View, live_transitions, replay, score  # noqa: E402
from swarm.fdlog import FdLog  # noqa: E402


def synth(me=1, n=5, dur=120.0, loss=0.0, pauses=None, hb=0.5, seed=1, observer_gap=None):
    """журнал наблюдателя me: соседи шлют heartbeat каждые hb с, каждый пакет теряется с вероятностью loss (на приёме у всех),
    сосед на паузе молчит; маска heard соседа — кого он сам слышал за 2.5 с"""
    rnd = random.Random(seed)
    pauses = pauses or {}
    ids = range(1, n + 1)
    last = {(k, j): None for k in ids for j in ids}      # k слышал j
    seq = {j: 0 for j in ids}
    lines = [{"k": "start", "t": 0.0, "me": me, "n": n}, {"k": "live", "t": 0.5}]
    t, t_tk = 1.0, 1.0
    while t < dur:
        for j in ids:
            if any(a <= t <= b for a, b in pauses.get(j, [])):
                continue
            seq[j] += 1
            for k in ids:
                if k == j or any(a <= t <= b for a, b in pauses.get(k, [])) or rnd.random() < loss:
                    continue
                last[(k, j)] = t
                if k == me and not (observer_gap and observer_gap[0] <= t <= observer_gap[1]):
                    h = sum(1 << (x - 1) for x in ids if x != j and last[(j, x)] is not None and t - last[(j, x)] < 2.5)
                    lines.append({"k": "hb", "t": round(t, 3), "s": j, "seq": seq[j], "hb": hb, "lq": round(100 * (1 - loss)), "h": h})
        if t >= t_tk and not (observer_gap and observer_gap[0] <= t <= observer_gap[1]):
            lines.append({"k": "tk", "t": round(t, 3), "lq": 1 - loss, "lossy": False, "alive": []})
            t_tk += 1.0
        elif observer_gap and observer_gap[0] <= t <= observer_gap[1]:
            t_tk = t + 1.0
        t = round(t + hb, 3)
    return lines


def test_fdlog_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_FD_LOG", raising=False)
    log = FdLog()
    assert not log.on
    log.write("hb", s=2)          # без файла — ничего не делает и не падает
    assert not log.tick_due()


def test_fdlog_writes_jsonl(tmp_path):
    clock = iter([100.0, 100.5, 101.2, 101.3]).__next__
    p = tmp_path / "sub" / "fd.jsonl"
    log = FdLog(str(p), clock=clock)
    log.write("hb", s=2, seq=7, hb=0.5, lq=93, h=6)
    assert log.tick_due()
    log.write("dead", j=3, lossy=True)
    log.f.flush()
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    assert rows == [{"k": "hb", "t": 100.0, "s": 2, "seq": 7, "hb": 0.5, "lq": 93, "h": 6},
                    {"k": "dead", "t": 101.2, "j": 3, "lossy": True}]


def test_loss_estimate_from_seq_gaps():
    v = View(1, 3)
    for i, seq in enumerate([1, 2, 4, 5, 7, 8, 10]):       # из 10 номеров пришло 7, ожидалось 9 после первого
        v.on_hb(float(i), 2, seq, 0.5, 100, 0)
    assert abs(v.peers[2].loss(6.0) - (1 - 6 / 9)) < 1e-9


def test_clean_channel_no_false_dead_and_fast_detection():
    pauses = {3: [(40.0, 70.0)]}
    lines = synth(loss=0.0, pauses=pauses)
    for det in (B0(), B1()):
        trans, active = replay(lines, 1, 5, det)
        r = score(trans, active, 1, pauses, [(0, 1e9, "L0")])
        assert r["false_dead"] == [], det.name
        assert len(r["detections"]) == 1 and not r["misses"], det.name
        assert 2.0 <= r["detections"][0]["latency"] <= 8.0, (det.name, r["detections"])


def test_b1_waits_longer_for_lossy_peer():
    """одинаковое молчание: у соседа с потерями 75 % вероятность «жив, но не дошло» выше, чем у чистого"""
    det = B1()
    probs = []
    for loss in (0.0, 0.75):
        v = View(1, 3)
        seq, t = 0, 0.0
        rnd = random.Random(5)
        while t < 30.0:
            seq += 1
            if rnd.random() >= loss:
                v.on_hb(t, 2, seq, 0.5, 100, 0)
            t += 0.5
        v.on_hb(t, 3, 1, 0.5, round(100 * (1 - loss)), 0)
        probs.append(det.p_alive_silent(v, 2, v.peers[2].last_rx + 4.0)[1])
    assert probs[0] < det.eps < probs[1]


def test_b0_replay_matches_coordinator_thresholds():
    """B0 по журналу: без потерь DEAD через T_DEAD после последнего пакета, если gossip о соседе пропал"""
    pauses = {2: [(50.0, 200.0)]}
    trans, _ = replay(synth(loss=0.0, dur=100.0, pauses=pauses), 1, 5, B0())
    dead = [t for t, j, k in trans if j == 2 and k == "dead"]
    assert len(dead) == 1 and 49.5 + 3.5 <= dead[0] <= 49.5 + 3.5 + 0.2 + 1e-9


def test_b1_still_detects_under_loss():
    pauses = {4: [(100.0, 150.0)]}
    lines = synth(loss=0.5, dur=200.0, pauses=pauses, seed=3)
    r = score(*replay(lines, 1, 5, B1()), 1, pauses, [(0, 1e9, "L2")])
    assert len(r["detections"]) == 1 and r["detections"][0]["latency"] <= 15.0 + 2.5 + 0.5


def test_observer_pause_not_scored():
    """свой простой наблюдателя: все соседи «молчат», но это не ложный DEAD"""
    pauses = {1: [(50.0, 80.0)]}
    lines = synth(loss=0.0, pauses=pauses, observer_gap=(50.0, 80.0))
    trans, active = replay(lines, 1, 5, B0())
    r = score(trans, active, 1, pauses, [(0, 1e9, "L0")])
    assert r["false_dead"] == []
    assert len(active) == 2


def test_false_dead_outside_pause_counted_and_grace_after_unpause():
    pauses = {2: [(10.0, 20.0)]}
    trans = [(15.0, 2, "dead"), (21.0, 2, "dead"), (40.0, 2, "dead"), (40.0, 3, "dead")]
    r = score(trans, [(0.0, 100.0)], 1, pauses, [(0, 30, "L1"), (30, 100, "L2")])
    assert [d["latency"] for d in r["detections"]] == [5.0]
    assert [(f["j"], f["level"]) for f in r["false_dead"]] == [(2, "L2"), (3, "L2")]
    assert r["exposure_s"] == {"L1": 30.0, "L2": 70.0}


def test_dead_across_l4_split_is_unreachable_not_false():
    trans = [(12.0, 4, "dead"), (12.0, 2, "dead"), (25.0, 5, "dead"), (40.0, 4, "dead")]
    r = score(trans, [(0.0, 100.0)], 1, {}, [(0, 100, "L4")], unreach=[(10.0, 20.0, [4, 5])])
    assert [u["j"] for u in r["unreachable"]] == [4, 5]          # 25.0 ещё в удержании 10 с после разрыва
    assert [f["j"] for f in r["false_dead"]] == [2, 4]


def test_missed_pause():
    r = score([], [(0.0, 100.0)], 1, {3: [(10.0, 20.0)]}, [(0, 100, "L0")])
    assert len(r["misses"]) == 1 and r["misses"][0]["dur"] == 10.0


def test_live_transitions_read_from_log():
    lines = [{"k": "dead", "t": 5.0, "j": 2, "lossy": False}, {"k": "hb", "t": 6.0}, {"k": "alive", "t": 7.0, "j": 2, "lossy": True}]
    assert live_transitions(lines) == [(5.0, 2, "dead"), (7.0, 2, "alive")]
