"""tests/test_replay.py — повторы старых heartbeat не должны продлевать жизнь пира."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.replay import SeqFilter  # noqa: E402


def test_in_order_and_gaps_accepted():
    f = SeqFilter()
    assert [f.accept(s) for s in (10, 11, 15, 16)] == [True] * 4


def test_duplicate_rejected():
    f = SeqFilter()
    f.accept(5)
    assert not f.accept(5)


def test_wraparound_is_newer():
    f = SeqFilter()
    f.accept(0xFFFE)
    assert f.accept(1)


def test_single_old_packet_replay_rejected():
    f = SeqFilter()
    for s in range(1000, 1010):
        f.accept(s)
    assert not f.accept(900)
    assert f.accept(1010)           # живой поток продолжается


def test_isolated_replays_never_accumulate_into_restart():
    f = SeqFilter()
    f.accept(1000)
    assert not f.accept(900)
    assert f.accept(1001)           # новый пакет сбрасывает счёт «старых»
    assert not f.accept(950)
    assert not f.accept(300)


def test_sender_restart_is_recognised_after_three_consecutive_packets():
    f = SeqFilter()
    f.accept(5000)
    assert not f.accept(0)
    assert not f.accept(1)
    assert f.accept(2)              # третий подряд идущий «старый» — это перезапуск
    assert f.accept(3)
