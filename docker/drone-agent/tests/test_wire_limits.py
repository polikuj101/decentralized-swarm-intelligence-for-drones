"""tests/test_wire_limits.py — проверка лимита 64 байта для всех сообщений протокола."""
import numpy as np

from swarm import wire
from swarm.embedder import quantize_int8


def test_heartbeat_size():
    body = wire.enc_hb(
        x=3200.0, y=-3200.0, z=25.0,
        state=2, flags=0xFF, sigma_m=45.0,
        task=255, epoch=255, link_q=100.0,
        heard=0xFF, hb_s=25.0,
    )
    assert len(body) == 14
    sealed = wire.seal(wire.T_HB, sender=1, seq=65535, t_ms=4294967295, body=body)
    assert len(sealed) == 26
    assert len(sealed) <= wire.MAX_PAYLOAD


def test_alloc_size():
    body = wire.enc_alloc(
        epoch=255, done=0xFFFFFFFF, split2=0xFF, split3=0xFF,
        assign=[255] * 8, alive=0xFF,
    )
    assert len(body) == wire._AL.size
    sealed = wire.seal(wire.T_ALLOC, sender=1, seq=65535, t_ms=4294967295, body=body)
    assert len(sealed) == 8 + wire._AL.size + 4
    assert len(sealed) <= wire.MAX_PAYLOAD


def test_claim_max_tasks_size():
    # 22 tasks
    y = [4095] * 22
    z = [15] * 22
    body = wire.enc_claim(epoch=255, y=y, z=z)
    assert len(body) == 2 + 2 * 22  # 46 bytes
    sealed = wire.seal(wire.T_CLAIM, sender=1, seq=65535, t_ms=4294967295, body=body)
    assert len(sealed) == 58
    assert len(sealed) <= wire.MAX_PAYLOAD


def test_fail_size():
    body = wire.enc_fail(failed=8, reason=3, epoch=255)
    assert len(body) == 3
    sealed = wire.seal(wire.T_FAIL, sender=1, seq=65535, t_ms=4294967295, body=body)
    assert len(sealed) == 15
    assert len(sealed) <= wire.MAX_PAYLOAD


def test_t_svec_reserved_size():
    # Зарезервированное сообщение T_SVEC: 8 заголовок + 32 int8 + 4 тег = 44 Б
    dummy_vec = np.random.randn(768).astype(np.float32)
    dummy_vec = dummy_vec / np.linalg.norm(dummy_vec)
    q = quantize_int8(dummy_vec[:32])
    assert len(q) == 32
    T_SVEC = 5
    sealed = wire.seal(T_SVEC, sender=1, seq=1, t_ms=1000, body=q)
    assert len(sealed) == 44
    assert len(sealed) <= wire.MAX_PAYLOAD
