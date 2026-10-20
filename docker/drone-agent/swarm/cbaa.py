"""swarm/cbaa.py — Consensus-Based Auction & Bundle Algorithm (CBAA / CBBA).
Чистая логика: поддержка бандлов (CBBA), гистерезис против флаппинга и справедливый tie-breaking.
"""


class CBAA:
    def __init__(self, me, m, eps=10, max_bundle=1):
        self.me = me
        self.m = m
        self.eps = eps
        self.max_bundle = max_bundle
        self.bundle = []
        self.reset({}, set())

    @property
    def x(self):
        """Обратная совместимость: текущая главная задача (первая в бандле) или None."""
        return self.bundle[0] if self.bundle else None

    @x.setter
    def x(self, val):
        if val is None:
            self.bundle = []
        elif not self.bundle or self.bundle[0] != val:
            self.bundle = [val]

    def reset(self, scores, avail):
        self.avail = set(avail)
        self.c = [int(scores.get(j, 0)) if j in self.avail else 0 for j in range(self.m)]
        self.y = [0] * self.m
        self.z = [0] * self.m
        self.bundle = []

    @staticmethod
    def _tie_hash(agent_id, task_id):
        """Детерминированный хэш Кнута для честного разрешения ничьих без перекоса в сторону ID=1."""
        return ((agent_id * 2654435761) ^ ((task_id + 1) * 2246822519)) & 0xFFFFFFFF

    def _better(self, y1, z1, y2, z2, task_id=0):
        """(y1, z1) лучше (y2, z2):
        1. Большая ставка побеждает.
        2. При равенстве ставок — справедливый детерминированный tie-break по хэшу задачи.
        """
        if y1 != y2:
            return y1 > y2
        if z1 == 0:
            return False
        if z2 == 0:
            return True
        h1 = self._tie_hash(z1, task_id)
        h2 = self._tie_hash(z2, task_id)
        if h1 != h2:
            return h1 > h2
        return z1 < z2

    def select(self):
        """Выбор задач в бандл до достижения max_bundle."""
        changed = False
        while len(self.bundle) < self.max_bundle:
            best, bj = 0, None
            for j in sorted(self.avail):
                if j in self.bundle:
                    continue
                cj = self.c[j]
                if cj <= 0:
                    continue
                # Анти-флаппинг: перехват чужой задачи требует превышения ставки на величину гистерезиса eps
                is_peer_task = (self.z[j] != 0 and self.z[j] != self.me)
                threshold = (self.y[j] + self.eps) if is_peer_task else self.y[j]
                if cj >= threshold and self._better(cj, self.me, self.y[j], self.z[j], j):
                    if cj > best:
                        best, bj = cj, j
            if bj is None:
                break
            self.bundle.append(bj)
            self.y[bj], self.z[bj] = best, self.me
            changed = True
        return changed

    def merge(self, y, z):
        """Слияние чужого вектора (y, z); True, если локальное состояние изменилось."""
        changed = False
        for j in range(self.m):
            if j in self.avail and self._better(y[j], z[j], self.y[j], self.z[j], j):
                self.y[j], self.z[j] = y[j], z[j]
                changed = True

        # Проверяем, не отобрали ли у нас какую-либо задачу из нашего бандла
        prev_bundle_len = len(self.bundle)
        self.bundle = [k for k in self.bundle if self.z[k] == self.me]
        if len(self.bundle) != prev_bundle_len:
            changed = True

        # Если освободилось место в бандле — пробуем добрать лучшую задачу
        if self.select():
            changed = True
        return changed

    def drop(self, done_tasks):
        """Задачи выполнены кем-то ещё."""
        self.avail -= set(done_tasks)
        prev_bundle_len = len(self.bundle)
        self.bundle = [k for k in self.bundle if k not in done_tasks]
        for j in done_tasks:
            self.c[j], self.y[j], self.z[j] = 0, 0, 0
        if len(self.bundle) != prev_bundle_len or self.select():
            return True
        return False

    def drop_peer(self, peer_id):
        """Борт погиб — сбросить все его победы в векторе z."""
        changed = False
        for j in range(self.m):
            if self.z[j] == peer_id:
                self.y[j], self.z[j] = 0, 0
                changed = True
        if self.select():
            changed = True
        return changed
