"""swarm/verdicts.py — итоговый вердикт решения System 1/2 по ответу координатора (last_cmd), а не по факту публикации.

system1_controller/planner_bridge ставят ACCEPTED в момент публикации команды; координатор потом может её отклонить
(stale, phase mismatch, not permitted, busy). Для HUD вердикт ACCEPTED превращается в APPLIED / REJECTED / UNCONFIRMED."""
from collections import OrderedDict

CONFIRM_TIMEOUT_S = 5.0
MAX_RESULTS = 64


class VerdictResolver:
    def __init__(self, timeout_s=CONFIRM_TIMEOUT_S):
        self.timeout_s = timeout_s
        self.results = OrderedDict()      # (source, id) -> (ok, msg)
        self.pending = []                 # [(t_added, thought)]

    def observe(self, last_cmd):
        if not last_cmd or last_cmd.get("id") is None or not last_cmd.get("source"):
            return
        self.results[(last_cmd["source"], last_cmd["id"])] = (bool(last_cmd.get("ok")), str(last_cmd.get("msg") or ""))
        while len(self.results) > MAX_RESULTS:
            self.results.popitem(last=False)

    def add(self, thought, now):
        """Возвращает thought сразу, если ждать нечего (не ACCEPTED), иначе None: придёт из drain()."""
        if thought.get("verdict") != "ACCEPTED":
            return thought
        self.pending.append((now, dict(thought)))
        return None

    def drain(self, now):
        out, keep = [], []
        for t_add, th in self.pending:
            res = self.results.get((th.get("source"), th.get("id")))
            if res is not None:
                th["verdict"], th["reason"] = ("APPLIED", th.get("reason") or "") if res[0] else ("REJECTED", res[1][:160])
                out.append(th)
            elif now - t_add >= self.timeout_s:
                th["verdict"], th["reason"] = "UNCONFIRMED", "координатор не подтвердил исполнение"
                out.append(th)
            else:
                keep.append((t_add, th))
        self.pending = keep
        return out
