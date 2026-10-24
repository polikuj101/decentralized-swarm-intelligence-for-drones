"""convergence.py — чистая логика «рой сошёлся после слияния» для web-hud. Без сети, Docker и FastAPI.

Критерий тот же, что в scripts/stress_test.py (v1_netem): одна эпоха у всех бортов, у задач нет двух разных
победителей, каждый борт видит всех живыми. Дополнительно (ревью 2026-10-24):
  * пакет старше MAX_AGE_S не считается — борт «не свежий», рой не сошёлся;
  * после Heal считаются только пакеты, принятые строго ПОСЛЕ момента Heal (равенство = неоднозначно = не считаем) (иначе состояние времён разделения
    может ложно дать «сошлось» сразу — тот же класс ошибок, что 2,2 с против 6,27 с в разделе 8.7);
  * дубликаты во время разделения (задачи, заявленные обеими группами, как в stress_test) и после слияния
    (две разные победителя у одной задачи) показываются отдельно; split_effective говорит, разошлась ли сеть вообще.

Время — монотонные секунды (time.monotonic()); таймер Heal стартует в момент, когда HUD проксировал restore.
Точность измерения ограничена периодом телеметрии бортов.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_AGE_S = 3.0          # как tele_age > 3 в app.js: борт без свежего пакета «не на связи»


@dataclass
class Check:
    converged: bool
    epochs: list
    dup: list
    all_alive: bool
    stale: list = field(default_factory=list)


def check(states: dict, n: int, now: float, since: float | None = None, max_age: float = MAX_AGE_S) -> Check:
    """states: {drone_id: {"epoch", "winners": {task: who}, "alive": [...], "rx": monotonic}} (rx=None если пакетов не было)."""
    stale, epochs, seen, dup, all_alive = [], set(), {}, set(), True
    for i in range(1, n + 1):
        st = states.get(i)
        rx = None if st is None else st.get("rx")
        if rx is None or now - rx > max_age or (since is not None and rx <= since):
            stale.append(i)
            continue
        if st.get("epoch") is not None:
            epochs.add(st["epoch"])
        for task, who in (st.get("winners") or {}).items():
            if task in seen and seen[task] != who:
                dup.add(task)
            seen[task] = who
        if len(st.get("alive") or []) != n:
            all_alive = False
    converged = not stale and len(epochs) == 1 and not dup and all_alive
    return Check(converged, sorted(epochs), sorted(dup), all_alive and not stale, stale)


def split_groups(n: int) -> tuple[list[int], list[int]]:
    half = (n + 1) // 2                  # n=5 -> {1,2,3}|{4,5}, как в stress_test v1
    return list(range(1, half + 1)), list(range(half + 1, n + 1))


def split_overlap(states: dict, n: int, now: float, max_age: float = MAX_AGE_S) -> tuple[bool, list]:
    """Дубликаты ВО ВРЕМЯ разделения — как dup_during_split в stress_test.py: задачи, у которых есть победитель
    в виде у обеих групп (пересечение ключей winners). Считается только когда разделение реально разошлось:
    все борты свежие и каждый видит живыми ровно свою группу; иначе (settled=False) пересечение — просто
    остаток общего распределения до разрыва, а не дубликат.
    Возвращает (settled, sorted_tasks)."""
    ga, gb = split_groups(n)
    tasks = {}
    for grp in (ga, gb):
        keys = set()
        for i in grp:
            st = states.get(i)
            rx = None if st is None else st.get("rx")
            if rx is None or now - rx > max_age or set(st.get("alive") or []) != set(grp):
                return False, []
            keys |= set((st.get("winners") or {}).keys())
        tasks[tuple(grp)] = keys
    return True, sorted(tasks[tuple(ga)] & tasks[tuple(gb)])


class Tracker:
    """idle -> split (Split) -> merging (Heal) -> converged. Любой новый Split начинает прогон заново."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.phase = "idle"
        self.t_split = self.t_heal = None
        self.converged_s = None
        self.dup_split: set = set()
        self.split_effective = False     # хотя бы раз все борты видели живой только свою группу
        self.epochs_merge: set = set()
        self.dup_merge: set = set()
        self.stale: list = []

    def mark_split(self, now: float) -> None:
        self.reset()
        self.phase, self.t_split = "split", now

    def mark_heal(self, now: float) -> None:
        if self.phase != "split":          # Heal без Split: не измеряем, нечего сливать
            return
        self.phase, self.t_heal = "merging", now

    @property
    def since(self) -> float | None:
        return self.t_heal if self.phase == "merging" else None

    def update(self, states: dict, n: int, now: float) -> None:
        if self.phase == "split":
            settled, dup = split_overlap(states, n, now)
            self.split_effective |= settled
            self.dup_split |= set(dup)
        elif self.phase == "merging":
            res = check(states, n, now, since=self.t_heal)
            self.epochs_merge |= set(res.epochs)
            self.dup_merge |= set(res.dup)
            self.stale = res.stale
            if res.converged:
                self.converged_s, self.phase = now - self.t_heal, "converged"

    def snapshot(self, now: float) -> dict:
        return {
            "phase": self.phase,
            "merge_s": None if self.converged_s is None else round(self.converged_s, 2),
            "elapsed_s": None if self.phase != "merging" else round(now - self.t_heal, 1),
            "split_effective": self.split_effective,
            "dup_split": sorted(self.dup_split),
            "dup_merge": sorted(self.dup_merge),
            "epochs_merge": sorted(self.epochs_merge),
            "waiting_for": self.stale,
        }
