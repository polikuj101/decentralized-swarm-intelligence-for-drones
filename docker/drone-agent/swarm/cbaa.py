"""swarm/cbaa.py — Consensus-Based Auction Algorithm (один тип назначения на борт). Чистая логика, без ROS."""


class CBAA:
    def __init__(self, me, m):
        self.me, self.m = me, m
        self.reset({}, set())

    def reset(self, scores, avail):
        self.avail = set(avail)
        self.c = [int(scores.get(j, 0)) if j in self.avail else 0 for j in range(self.m)]
        self.y = [0] * self.m
        self.z = [0] * self.m
        self.x = None

    @staticmethod
    def _better(y1, z1, y2, z2):
        """(y1,z1) лучше (y2,z2): большая ставка; при равенстве — меньший ID победителя"""
        if y1 != y2:
            return y1 > y2
        return z1 != 0 and (z2 == 0 or z1 < z2)

    def select(self):
        if self.x is not None:
            return False
        best, bj = 0, None
        for j in sorted(self.avail):
            cj = self.c[j]
            if cj > best and self._better(cj, self.me, self.y[j], self.z[j]):
                best, bj = cj, j
        if bj is None:
            return False
        self.x = bj
        self.y[bj], self.z[bj] = best, self.me
        return True

    def merge(self, y, z):
        """слияние чужого вектора; True, если локальное состояние изменилось"""
        changed = False
        for j in range(self.m):
            if j in self.avail and self._better(y[j], z[j], self.y[j], self.z[j]):
                self.y[j], self.z[j] = y[j], z[j]
                changed = True
        if self.x is not None and self.z[self.x] != self.me:
            self.x = None
            changed = True
        if self.select():
            changed = True
        return changed

    def drop(self, done_tasks):
        """задачи выполнены кем-то ещё"""
        self.avail -= set(done_tasks)
        if self.x in done_tasks:
            self.x = None
        for j in done_tasks:
            self.c[j], self.y[j], self.z[j] = 0, 0, 0
        self.select()

    def drop_peer(self, peer_id):
        """борт погиб — сбросить все его победы в векторе z"""
        changed = False
        for j in range(self.m):
            if self.z[j] == peer_id:
                self.y[j], self.z[j] = 0, 0
                changed = True
        if self.select():
            changed = True
        return changed
