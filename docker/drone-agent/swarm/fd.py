"""swarm/fd.py — детекторы отказа соседа и их офлайн-проверка по журналу swarm/fdlog.py (docs/PLAN_LEARNED_FAILURE_DETECTOR.md).

View   — что борт знает о соседях: последние приёмы, номера пакетов (доля потерь), gossip `heard`.
B0     — нынешнее правило координатора (alive_set): прямой приём или gossip за T_DEAD (8 с в режиме потерь).
B1     — вероятностный порог: сосед молчит s секунд, вероятность «жив, но мы не получили ни одного свидетельства» ≈ P_miss^(s/hb - 1),
         где P_miss — вероятность за один период heartbeat не получить ни прямой пакет, ни gossip через соседей. DEAD при P < eps.
         Ограждение правилами: свидетельство моложе t_min — жив, старше t_max — мёртв, модель решает только между ними.
replay — прогнать детектор по журналу одного борта; score — сверить переходы с паузами контейнеров (метка «мёртв»).
"""
from collections import deque

from swarm.features import GAP_LOSSY_S, LOSSY_HOLD_S, dead_after

LOSS_WINDOW_S = 30.0
GAP_RESET = 200          # пропуск номеров больше этого — перезапуск отправителя, а не потери


class PeerView:
    __slots__ = ("last_rx", "seq", "hb", "lq", "heard", "rx_log")

    def __init__(self):
        self.last_rx, self.seq, self.hb, self.lq, self.heard = None, None, 0.5, 100, 0
        self.rx_log = deque()          # (t, ожидалось пакетов с прошлого приёма) за LOSS_WINDOW_S

    def loss(self, t):
        """доля потерянных heartbeat этого соседа на приёме у меня за последние LOSS_WINDOW_S; None — данных мало"""
        while self.rx_log and t - self.rx_log[0][0] > LOSS_WINDOW_S:
            self.rx_log.popleft()
        if len(self.rx_log) < 3:
            return None
        exp = sum(g for _, g in self.rx_log)
        return 1.0 - len(self.rx_log) / exp


class View:
    """состояние наблюдателя: тот же приём heartbeat, что в coordinator._on_hb (повторы уже отброшены SeqFilter до журнала)"""

    def __init__(self, me, n):
        self.me = me
        self.peers = {j: PeerView() for j in range(1, n + 1) if j != me}
        self.lossy_until = 0.0
        self.gossip_last = {}          # j -> когда я последний раз получил пакет соседа, где он сообщает, что слышит j

    def on_hb(self, t, s, seq, hb, lq, h):
        p = self.peers.get(s)
        if p is None:
            return
        if p.seq is not None:
            gap = (seq - p.seq) & 0xFFFF
            if 1 <= gap <= GAP_RESET:
                p.rx_log.append((t, gap))
            else:
                p.rx_log.clear()
        if p.last_rx is not None and t - p.last_rx >= GAP_LOSSY_S:
            self.lossy_until = max(self.lossy_until, t + LOSSY_HOLD_S)
        p.last_rx, p.seq, p.hb, p.lq, p.heard = t, seq, max(0.5, hb), lq, h
        for j in self.peers:
            if j != s and (h >> (j - 1)) & 1:
                self.gossip_last[j] = t

    def gossip_rx(self, j, t, horizon):
        """когда я последний раз получил от кого-то из соседей пакет, где он сообщает, что слышит j (не старше horizon)"""
        best = None
        for k, pk in self.peers.items():
            if k != j and pk.last_rx is not None and t - pk.last_rx < horizon and (pk.heard >> (j - 1)) & 1:
                best = pk.last_rx if best is None else max(best, pk.last_rx)
        return best


class B0:
    """нынешний coordinator.alive_set без force_dead (FAIL — следствие System 1, его оцениваем отдельно)"""
    name = "B0"

    def alive(self, v, t):
        t_dead = dead_after(t < v.lossy_until)
        out = set()
        for j, p in v.peers.items():
            if p.last_rx is not None and t - p.last_rx < t_dead:
                out.add(j)
            elif v.gossip_rx(j, t, t_dead) is not None:
                out.add(j)
        return out


class B1:
    def __init__(self, eps=1e-3, t_min=3.0, t_max=15.0, p_floor=0.05, p_default=0.3, g_age=0.0):
        # g_age: бит heard значит «слышал за последние 2.5 с» (coordinator.heard_mask), свидетельство через gossip старше момента приёма
        self.eps, self.t_min, self.t_max, self.p_floor, self.p_default, self.g_age = eps, t_min, t_max, p_floor, p_default, g_age
        self.name = f"B1(eps={eps:g},t_min={t_min:g},g_age={g_age:g})"

    def _p(self, x):
        return min(0.99, max(self.p_floor, self.p_default if x is None else x))

    def p_alive_silent(self, v, j, t):
        """вероятность, что j жив, а за время молчания до меня не дошло ни одного свидетельства; (молчание, P)"""
        pj = v.peers[j]
        ev = pj.last_rx
        g = v.gossip_last.get(j)
        g = None if g is None else g - self.g_age
        if g is not None and (ev is None or g > ev):
            ev = g
        if ev is None:
            return None, 0.0
        s = t - ev
        miss = self._p(pj.loss(t))                         # прямой пакет от j потерян
        for k, pk in v.peers.items():                      # и каждый путь через k: k не услышал j или я не услышал k
            if k == j or pk.last_rx is None or t - pk.last_rx >= self.t_max:
                continue
            p_kj = self._p(1.0 - pk.lq / 100.0)            # потери на приёме у k: его link_q из heartbeat
            p_ik = self._p(pk.loss(t))
            miss *= 1.0 - (1.0 - p_kj) * (1.0 - p_ik)
        n = max(0.0, s / pj.hb - 1.0)
        return s, miss ** n

    def alive(self, v, t):
        out = set()
        for j in v.peers:
            s, p = self.p_alive_silent(v, j, t)
            if s is None:
                continue
            if s < self.t_min or (s < self.t_max and p >= self.eps):
                out.add(j)
        return out


def replay(lines, me, n, detector, dt=0.2):
    """прогнать детектор по журналу одного борта. lines — dict-строки журнала по возрастанию t.
    Возвращает (переходы [(t, j, 'dead'|'alive')], интервалы работы [(t0, t1)]): до приказа (live) и при разрыве тиков
    журнала (борт сам стоял на паузе) liveness не считается; после такого разрыва первый вывод детектора берётся как исходный."""
    v = View(me, n)
    trans, active = [], []
    live, prev, t_grid, t_last = False, None, None, None
    seg0 = None
    for ln in lines:
        t, k = ln["t"], ln["k"]
        if live and t_last is not None and t - t_last > 3.0:      # разрыв журнала: наблюдатель сам не работал
            if seg0 is not None:
                active.append((seg0, t_last))
            seg0, prev, t_grid = None, None, None
        if live and t_grid is not None:
            while t_grid <= t:
                a = detector.alive(v, t_grid)
                if prev is not None:
                    for j in sorted(prev - a):
                        trans.append((t_grid, j, "dead"))
                    for j in sorted(a - prev):
                        trans.append((t_grid, j, "alive"))
                prev = a
                t_grid += dt
        if k == "hb":
            v.on_hb(t, ln["s"], ln["seq"], ln["hb"], ln["lq"], ln["h"])
        elif k == "live":
            live = True
        elif k == "start":
            v, live, prev, t_grid = View(me, n), False, None, None
            if seg0 is not None and t_last is not None:
                active.append((seg0, t_last))
            seg0 = None
        if live and t_grid is None and k in ("tk", "hb", "live"):
            t_grid, seg0 = t, t
        t_last = t
    if seg0 is not None and t_last is not None:
        active.append((seg0, t_last))
    return trans, active


def live_transitions(lines):
    """живые решения координатора (B0 на борту) из того же журнала: для сверки, что replay(B0) воспроизводит борт"""
    return [(ln["t"], ln["j"], ln["k"]) for ln in lines if ln["k"] in ("dead", "alive")]


def _inside(t, ivs, pad=0.0):
    return any(a - pad <= t <= b + pad for a, b in ivs)


def score(trans, active, me, pauses, levels, unreach=(), grace_s=2.0, settle_s=5.0, unreach_hold_s=10.0):
    """сверка переходов одного наблюдателя с паузами контейнеров.
    pauses: {борт: [(t_pause, t_unpause)]}; levels: [(t0, t1, 'L0'..'L4')];
    unreach: [(t0, t1, группа)] — жёсткий разрыв L4 группы от остальных: DEAD через разрыв (и unreach_hold_s после) не ложный,
    а «недостижим», считается отдельно (метка «физически жив» основная, см. план).
    Наблюдатель сам на паузе и settle_s после неё не оценивается. DEAD соседа во время его паузы — обнаружение (задержка от начала паузы),
    до grace_s после снятия паузы — не считается, иначе — ложный. Пауза, за которую наблюдатель не объявил DEAD, — пропуск."""
    own = [(a, b + settle_s) for a, b in pauses.get(me, [])]

    def level_at(t):
        for a, b, lv in levels:
            if a <= t < b:
                return lv
        return "?"

    false_dead, unreachable, det = [], [], {}
    for t, j, kind in trans:
        if kind != "dead" or _inside(t, own) or not _inside(t, active):
            continue
        if any(a <= t <= b + unreach_hold_s and ((me in g) != (j in g)) for a, b, g in unreach):
            unreachable.append({"t": t, "j": j})
            continue
        hit = None
        for i, (a, b) in enumerate(pauses.get(j, [])):
            if a <= t <= b:
                hit = (j, i)
                break
            if b < t <= b + grace_s:
                hit = ()
                break
        if hit is None:
            false_dead.append({"t": t, "j": j, "level": level_at(t)})
        elif hit and hit not in det:
            det[hit] = t
    detections, misses = [], []
    for j, ivs in pauses.items():
        if j == me:
            continue
        for i, (a, b) in enumerate(ivs):
            covered = any(x <= a and b <= y for x, y in active) and not any(x < b and a < y for x, y in own)
            if not covered:
                continue
            if (j, i) in det:
                detections.append({"j": j, "t_pause": a, "latency": det[(j, i)] - a, "level": level_at(a)})
            else:
                misses.append({"j": j, "t_pause": a, "dur": b - a, "level": level_at(a)})
    exposure = {}
    for a, b in active:
        for la, lb, lv in levels:
            x, y = max(a, la), min(b, lb)
            if y > x:
                exposure[lv] = exposure.get(lv, 0.0) + (y - x)
    return {"false_dead": false_dead, "unreachable": unreachable, "detections": detections, "misses": misses,
            "exposure_s": exposure}
