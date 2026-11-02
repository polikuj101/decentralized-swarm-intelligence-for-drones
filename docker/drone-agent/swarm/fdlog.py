"""swarm/fdlog.py — журнал для детектора отказа соседа (docs/PLAN_LEARNED_FAILURE_DETECTOR.md).

Пишет сырые события приёма, а не готовые признаки: по ним офлайн воспроизводится любой детектор (B0, B1, M) и любые признаки.
Время t — часы хоста (time.time()), чтобы сверять с `docker events` (метка «мёртв» = пауза контейнера).
Включается SWARM_FD_LOG=<путь к .jsonl>, по умолчанию выключено. Ошибки записи журнала не трогают координатор.

Строки (k — вид):
  start  me, n                                  запуск координатора
  live                                          приказ получен, с этого момента работает liveness
  hb     s, seq, hb, lq, h                      принятый heartbeat (после фильтра повторов): период, link_q соседа 0..100, маска heard
  fail   s, f, ign                              принятый FAIL: кто объявил, про кого, проигнорирован ли (слышу сам)
  dead / alive  j, lossy                        переход в alive_set координатора (живое решение B0)
  tk     lq, lossy, alive                       раз в секунду: своё link_q, режим потерь, alive_set; разрыв тиков = борт сам стоял
"""
import json
import os
import time


class FdLog:
    FLUSH_S = 1.0

    def __init__(self, path=None, clock=time.time):
        self.path = path if path is not None else os.environ.get("SWARM_FD_LOG", "")
        self.clock = clock
        self.f, self.t_flush, self.t_tick, self.errors = None, 0.0, 0.0, 0
        if self.path:
            try:
                d = os.path.dirname(self.path)
                if d:
                    os.makedirs(d, exist_ok=True)
                self.f = open(self.path, "a", encoding="utf-8")
            except OSError:
                self.f, self.errors = None, 1

    @property
    def on(self):
        return self.f is not None

    def write(self, k, **kw):
        if self.f is None:
            return
        t = self.clock()
        try:
            self.f.write(json.dumps({"k": k, "t": round(t, 3), **kw}, separators=(",", ":")) + "\n")
            if t - self.t_flush >= self.FLUSH_S:
                self.f.flush()
                self.t_flush = t
        except (OSError, ValueError):
            self.errors += 1
            if self.errors > 100:          # диск полон или файл закрыт: выключить, а не сыпать ошибками каждый такт
                self.f = None

    def close(self):
        if self.f is not None:
            try:
                self.f.close()
            except OSError:
                pass
            self.f = None

    def tick_due(self):
        """раз в секунду: строка tk"""
        if self.f is None:
            return False
        t = self.clock()
        if t - self.t_tick >= 1.0:
            self.t_tick = t
            return True
        return False


def mask_ids(m):
    return [k for k in range(1, 9) if (m >> (k - 1)) & 1]
