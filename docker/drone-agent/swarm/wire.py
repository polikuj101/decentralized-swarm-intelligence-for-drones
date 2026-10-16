"""swarm/wire.py — бинарный протокол роя. Любой пакет <= 64 байт, включая 4-байтовый HMAC-тег."""
import hashlib
import hmac
import os
import struct

KEY = bytes.fromhex(os.environ.get("SWARM_KEY", "00" * 16))
MAX_PAYLOAD = 64
TAG_LEN = 4
HDR = struct.Struct("<BBHI")
T_HB, T_CLAIM, T_ALLOC, T_FAIL = 1, 2, 3, 4
NONE = 255
FL_GPS_OK, FL_VIO_OK, FL_NAV_DEG, FL_LLM_OK, FL_AIRBORNE, FL_GPS_SUSPECT = 1, 2, 4, 8, 16, 32
REASONS = {"NO_HEARTBEAT": 0, "GPS_LOST": 1, "LINK_DEGRADED": 2, "NAV_DRIFT": 3}

_HB = struct.Struct("<hhhBBBBBBBB")
_AL = struct.Struct("<BIBB8sB")
_FL = struct.Struct("<BBB")

assert HDR.size + _HB.size + TAG_LEN <= MAX_PAYLOAD
assert HDR.size + _AL.size + TAG_LEN <= MAX_PAYLOAD
assert HDR.size + _FL.size + TAG_LEN <= MAX_PAYLOAD
assert HDR.size + 2 + 2 * 22 + TAG_LEN <= MAX_PAYLOAD


def _i16(v):
    return max(-32768, min(32767, int(round(v))))


def _u8(v):
    return max(0, min(255, int(round(v))))


def _tag(raw):
    return hmac.new(KEY, raw, hashlib.sha256).digest()[:TAG_LEN]


def seal(mtype, sender, seq, t_ms, body):
    raw = HDR.pack(mtype, sender, seq & 0xFFFF, t_ms & 0xFFFFFFFF) + body
    out = raw + _tag(raw)
    if len(out) > MAX_PAYLOAD:
        raise ValueError(f"message {len(out)} B > {MAX_PAYLOAD} B")
    return out


def unseal(data):
    """-> (type, sender, seq, t_ms, body) или None при неверной длине/теге"""
    if len(data) < HDR.size + TAG_LEN or len(data) > MAX_PAYLOAD:
        return None
    raw, tag = data[:-TAG_LEN], data[-TAG_LEN:]
    if not hmac.compare_digest(_tag(raw), tag):
        return None
    mtype, sender, seq, t_ms = HDR.unpack_from(raw)
    return mtype, sender, seq, t_ms, raw[HDR.size:]


def enc_hb(x, y, z, state, flags, sigma_m, task, epoch, link_q, heard, hb_s):
    return _HB.pack(_i16(x * 10), _i16(y * 10), _i16(z * 10), state & 0xFF, flags & 0xFF,
                    _u8(sigma_m * 2), task & 0xFF, epoch & 0xFF, _u8(link_q), heard & 0xFF, _u8(hb_s * 10))


def dec_hb(b):
    x, y, z, st, fl, sg, tk, ep, lq, hd, hp = _HB.unpack(b)
    return {"x": x / 10.0, "y": y / 10.0, "z": z / 10.0, "state": st, "flags": fl, "sigma": sg / 2.0,
            "task": tk, "epoch": ep, "link_q": lq, "heard": hd, "hb_s": hp / 10.0}


def enc_claim(epoch, y, z):
    b = bytearray(struct.pack("<BB", epoch & 0xFF, len(y)))
    for yy, zz in zip(y, z):
        b += struct.pack("<H", ((zz & 0xF) << 12) | min(int(yy), 4095))
    return bytes(b)


def dec_claim(b):
    epoch, n = struct.unpack_from("<BB", b)
    if len(b) != 2 + 2 * n:
        raise ValueError("claim length")
    y, z = [], []
    for i in range(n):
        (v,) = struct.unpack_from("<H", b, 2 + 2 * i)
        y.append(v & 0x0FFF)
        z.append(v >> 12)
    return {"epoch": epoch, "y": y, "z": z}


def enc_alloc(epoch, done, split2, split3, assign, alive):
    a = bytes((assign + [NONE] * 8)[:8])
    return _AL.pack(epoch & 0xFF, done & 0xFFFFFFFF, split2 & 0xFF, split3 & 0xFF, a, alive & 0xFF)


def dec_alloc(b):
    ep, dn, s2, s3, a, al = _AL.unpack(b)
    return {"epoch": ep, "done": dn, "split2": s2, "split3": s3, "assign": list(a), "alive": al}


def enc_fail(failed, reason, epoch):
    return _FL.pack(failed & 0xFF, reason & 0xFF, epoch & 0xFF)


def dec_fail(b):
    f, r, e = _FL.unpack(b)
    return {"failed": f, "reason": r, "epoch": e}
