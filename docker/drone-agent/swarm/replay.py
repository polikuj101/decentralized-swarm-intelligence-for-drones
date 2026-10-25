"""swarm/replay.py — защита heartbeat от повторов старых пакетов (тег HMAC только 4 байта, nonce нет)."""

RESTART_RUN = 3          # столько подряд «старых» seq, идущих по возрастанию, считаем перезапуском отправителя
RESTART_STEP_MAX = 8


class SeqFilter:
    def __init__(self):
        self.last = None
        self._old_run, self._old_prev = 0, None

    def accept(self, seq: int) -> bool:
        if self.last is None:
            self.last = seq
            return True
        gap = (seq - self.last) & 0xFFFF
        if gap == 0:
            return False                                  # дубль
        if gap < 0x8000:                                  # новее последнего (с учётом переполнения u16)
            self.last, self._old_run, self._old_prev = seq, 0, None
            return True
        # «старее»: либо повтор записанного пакета, либо отправитель перезапустился и считает с нуля
        if self._old_prev is not None and 1 <= ((seq - self._old_prev) & 0xFFFF) <= RESTART_STEP_MAX:
            self._old_run += 1
        else:
            self._old_run = 1
        self._old_prev = seq
        if self._old_run >= RESTART_RUN:
            self.last, self._old_run, self._old_prev = seq, 0, None
            return True
        return False
