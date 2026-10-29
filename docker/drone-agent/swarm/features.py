"""swarm/features.py — состояние -> типизированный Obs -> бакеты -> каноническая строка (закрытый словарь)."""
from dataclasses import dataclass

T_SUSPECT = 3.0
T_SUSPECT_LOSSY = 10.0       # при плохой связи молчание соседа ожидаемо (потери), объявлять отказ рано: DECLARE_FAILOVER рвёт задачи живого борта
LINK_OK = 0.6


def suspect_after(link_q: float) -> float:
    return T_SUSPECT if link_q >= LINK_OK else T_SUSPECT_LOSSY


def silent_for(now: float, last_rx, t_live0: float) -> float:
    """сколько секунд сосед молчит. Отсчёт не раньше t_live0: пока борт сам не вышел в эфир (ждёт приказ), соседи тоже молчат,
    и это не отказ (иначе в момент приказа фаза ISOLATED и ложные DECLARE_FAILOVER)."""
    return now - max(t_live0, last_rx if last_rx is not None else t_live0)


@dataclass(frozen=True)
class Obs:
    uptime_s: float
    n_total: int
    n_alive: int
    link_q: float
    gps_fix: int
    gps_suspect: bool
    sigma: float
    min_silent_s: float
    max_silent_s: float
    n_suspect: int
    idle: bool
    task_kind: str          # none | SECTOR | TARGET
    n_open_targets: int
    withdrawn: bool
    override_active: bool

    def buckets(self, phase) -> dict:
        s = self.sigma
        dead = (self.n_total - self.n_alive) / max(1, self.n_total)
        return {
            "phase": phase.value.lower(),
            "link": "good" if self.link_q >= 0.7 else "fair" if self.link_q >= 0.4 else "poor",
            "gps": "lost" if self.gps_fix < 3 else "suspect" if self.gps_suspect else "ok",
            "sigma": "low" if s < 5 else "mid" if s < 20 else "high" if s < 40 else "critical",
            "silent": "long" if self.n_suspect > 0 else "short" if self.max_silent_s >= 3.0 else "none",
            "lost": "none" if dead == 0 else "some" if dead < 0.5 else "half" if dead < 0.8 else "most",
            "task": "idle" if self.idle else ("target" if self.task_kind == "TARGET" else "sector"),
        }

    def text(self, phase) -> str:
        return render(self.buckets(phase))


def render(b: dict) -> str:
    return (f"phase {b['phase']}. link {b['link']}. gps {b['gps']}. sigma {b['sigma']}. "
            f"silent {b['silent']}. lost {b['lost']}. task {b['task']}.")


def build_obs(st: dict) -> Obs:
    nav = st.get("nav") or {}
    peers = st["peers"]
    live = [p["silent_s"] for p in peers.values() if not p["declared_failed"]]
    name = st.get("task_name") or ""
    return Obs(
        uptime_s=float(st.get("t_live", st["t"])), n_total=len(peers) + 1, n_alive=len(st["alive"]), link_q=float(st["link_q"]),
        gps_fix=int(nav.get("gps_fix", 3)), gps_suspect=bool(nav.get("gps_suspect")), sigma=float(nav.get("sigma", 0.0)),
        min_silent_s=min(live) if live else 0.0, max_silent_s=max(live) if live else 0.0,
        n_suspect=sum(1 for p in peers.values()
                      if not p["alive"] and not p["declared_failed"] and p["silent_s"] >= suspect_after(float(st["link_q"]))),
        idle=st.get("task") is None, task_kind="TARGET" if name.startswith("T") else "SECTOR" if name else "none",
        n_open_targets=sum(1 for t in st["open"] if t["kind"] == "TARGET"),
        withdrawn=bool(st.get("withdrawn")), override_active=st.get("override") is not None)
