"""swarm/bump_gate.py — когда повышать эпоху после смены состава живых бортов (alive-set).

Каждый bump перезапускает аукцион CBAA у всего роя, поэтому лишние bump'ы — это лишние перезапуски.
Раньше bump шёл по переднему фронту с защитным интервалом GUARD_S: после слияния сети пиры узнавались
волнами (сначала один, потом остальные), первый bump срабатывал сразу, второй откладывался на GUARD_S —
рой дважды перезапускал аукцион, причём уже после того, как телеметрия показывала «сошлось».

Теперь для ПРИБЫТИЯ пиров bump идёт по заднему фронту: ждём, пока набор живых перестанет расти
(JOIN_SETTLE_S тишины), а если за это время приняли чужую более новую эпоху — свой bump отменяем (absorb).
Потеря пира по-прежнему обрабатывается сразу после защитного интервала: срочность перераспределения задач
важнее числа эпох.
"""
GUARD_S = 3.0          # минимум между двумя bump'ами (прежнее поведение)
JOIN_SETTLE_S = 1.0    # сколько набор живых должен не расти, прежде чем bump по прибытию пиров


class BumpGate:
    def __init__(self, guard_s: float = GUARD_S, join_settle_s: float = JOIN_SETTLE_S) -> None:
        self.guard_s, self.join_settle_s = guard_s, join_settle_s
        self.pending, self.join_only, self.changed_at = False, True, 0.0

    def observe(self, joined: bool, left: bool, now: float) -> None:
        """Вызывать в момент, когда alive-set изменился."""
        if not self.pending:
            self.pending, self.join_only = True, True
        if left:
            self.join_only = False          # потеря пира в окне ожидания: срочный режим
        self.changed_at = now

    def due(self, now: float, last_bump: float) -> bool:
        if not self.pending or now - last_bump <= self.guard_s:
            return False
        if self.join_only and now - self.changed_at < self.join_settle_s:
            return False
        return True

    def absorb(self) -> None:
        """Приняли чужую, более новую эпоху: аукцион уже перезапущен для всех (adopt делает reset_cbaa с нашим текущим
        составом живых), поэтому наш отложенный bump по ПРИБЫТИЮ пиров лишний. Без этого каждый борт бампит по своему
        таймеру, и N бортов дают N эпох подряд за те же 0,2 с. Срочный bump (потеря пира) не отменяется."""
        if self.pending and self.join_only:
            self.pending = False

    def fire(self) -> None:
        self.pending, self.join_only = False, True
