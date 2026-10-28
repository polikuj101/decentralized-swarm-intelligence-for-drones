"""swarm/mesh.py — Zenoh peer-сессия по mesh-net. Только доставка байтов; логика протокола — в coordinator."""
import logging
import threading
import time

import zenoh

from swarm.mesh_cfg import cfg

log = logging.getLogger("mesh")
PRIO = {"fail": "REAL_TIME", "claim": "INTERACTIVE_HIGH", "alloc": "INTERACTIVE_LOW", "hb": "DATA"}


_cfg = cfg


class Mesh:
    def __init__(self, me, own_ip, peer_ips, on_msg):
        self.me, self.on_msg = me, on_msg
        self.ready = False
        self.tuned = None
        self.tx_bytes = {k: 0 for k in PRIO}
        self.rx_bad = 0
        self.session = None
        self.pubs = {}
        threading.Thread(target=self._open, args=(own_ip, peer_ips), daemon=True).start()

    def _open(self, own_ip, peer_ips):
        while self.session is None:
            for tuned in (True, False):
                try:
                    self.session = zenoh.open(zenoh.Config.from_json5(_cfg(own_ip, peer_ips, tuned)))
                    self.tuned = tuned
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("zenoh open (tuned=%s) failed: %s", tuned, e)
            if self.session is None:
                time.sleep(2.0)
        self.session.declare_subscriber("swarm/v1/**", self._cb)
        for kind, prio in PRIO.items():
            self.pubs[kind] = self.session.declare_publisher(
                f"swarm/v1/{kind}/{self.me}",
                congestion_control=zenoh.CongestionControl.DROP,
                priority=getattr(zenoh.Priority, prio))
        self.ready = True
        log.info("zenoh ready, tuned=%s", self.tuned)

    def _cb(self, sample):
        try:
            data = sample.payload.to_bytes()
        except AttributeError:
            data = bytes(sample.payload)
        self.on_msg(data)

    def send(self, kind, data):
        if not self.ready:
            return False
        try:
            self.pubs[kind].put(data)
            self.tx_bytes[kind] += len(data)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("put failed: %s", e)
            return False

    def peers_connected(self):
        try:
            return len(list(self.session.info.peers_zid()))
        except Exception:  # noqa: BLE001
            return -1
