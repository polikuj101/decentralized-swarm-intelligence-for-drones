"""swarm/matcher.py — детерминированный gate (правила) ⊕ мягкая оценка близости к эталонам (эмбеддинг, асинхронно, кэш)."""
import json
import queue
import threading
from collections import OrderedDict, deque

from swarm.actions import Phase

COOLDOWN_S = {"DECLARE_FAILOVER": 20.0, "EVADE_JAMMING": 60.0}
DEFAULT_COOLDOWN_S = 30.0


class Decision:
    __slots__ = ("action", "score", "path", "scores")

    def __init__(self, action=None, score=0.0, path="idle", scores=None):
        self.action, self.score, self.path, self.scores = action, score, path, scores or {}


def rule_score(a, o, phase: Phase) -> float:
    """срочность действия [0..1]; 0 = не применимо (жёсткий gate). Инструменты только для S2 здесь не оцениваются."""
    t = a.tool
    if t == "DECLARE_FAILOVER":
        return 0.95 if o.n_suspect > 0 else 0.0
    if t == "RETURN_HOME":
        return 0.95 if phase == Phase.CRITICAL else 0.6 if (phase == Phase.ISOLATED and o.sigma > 20) else 0.0
    if t == "HOLD_POSITION":
        return 0.8 if phase == Phase.CRITICAL else 0.7 if (phase == Phase.DEGRADED_NAV and o.sigma > 20) else 0.0
    if t == "EVADE_JAMMING":
        return 0.75 if phase in (Phase.DEGRADED_COMMS, Phase.ISOLATED) else 0.0
    if t == "HOLD_SECTOR":
        return 0.65 if (phase == Phase.DEGRADED_NAV and o.sigma > 10) else 0.55 if phase == Phase.ISOLATED else 0.0
    if t == "SCAN_TARGET":
        return 0.55 if (o.idle and o.n_open_targets > 0) else 0.0
    return 0.0


class Matcher:
    def __init__(self, mode="hybrid", embedder=None, protos=None, tau=0.5, w_rule=0.6, cache_size=512):
        self.mode = mode if (embedder is not None and protos is not None and mode != "rules") else "rules"
        self.emb, self.tau, self.w, self.cache_size = embedder, tau, w_rule, cache_size
        self.cache, self.lock = OrderedDict(), threading.Lock()
        self.until, self.pending = {}, set()
        self.embed_ms = deque(maxlen=200)
        self.q = queue.Queue(maxsize=8)
        if self.mode != "rules":
            self.P, self.ptools = protos
            self.tools = sorted(set(self.ptools))
            self.idx = {t: [i for i, x in enumerate(self.ptools) if x == t] for t in self.tools}
            threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def key_of(a) -> str:
        return a.tool + json.dumps(a.args.model_dump(), sort_keys=True)

    def _worker(self):
        import time
        while True:
            text = self.q.get()
            t0 = time.perf_counter()
            try:
                v = self.emb.encode(text)
                s = self.P @ v
                sims = {t: float(max(s[i] for i in ix)) for t, ix in self.idx.items()}
                with self.lock:
                    self.cache[text] = sims
                    while len(self.cache) > self.cache_size:
                        self.cache.popitem(last=False)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.pending.discard(text)
                self.embed_ms.append((time.perf_counter() - t0) * 1000.0)

    def _sims(self, text):
        with self.lock:
            s = self.cache.get(text)
            if s is not None:
                self.cache.move_to_end(text)
                return s
        if text not in self.pending:
            self.pending.add(text)
            try:
                self.q.put_nowait(text)
            except queue.Full:
                self.pending.discard(text)
        return None

    def decide(self, cands, o, phase, now) -> Decision:
        scored = []
        for a in cands:
            k = self.key_of(a)
            if self.until.get(k, 0.0) > now:
                continue
            r = rule_score(a, o, phase)
            if r > 0.0:
                scored.append((a, k, r))
        if not scored:
            return Decision(path="idle")
        sims, path = None, "rules"
        if self.mode != "rules":
            sims = self._sims(o.text(phase))
            path = "hybrid" if sims is not None else "rules+miss"
        lo = hi = 0.0
        if sims:
            lo, hi = min(sims.values()), max(sims.values())
        best, scores = None, {}
        for a, k, r in scored:
            e = 0.5 if not sims or hi - lo < 1e-3 else (sims.get(a.tool, lo) - lo) / (hi - lo)
            s = self.w * r + (1.0 - self.w) * e
            scores[a.tool] = round(s, 3)
            if best is None or s > best[2]:
                best = (a, k, s)
        if best[2] < self.tau:
            return Decision(path=path, scores=scores)
        self.until[best[1]] = now + COOLDOWN_S.get(best[0].tool, DEFAULT_COOLDOWN_S)
        return Decision(best[0], best[2], path, scores)
