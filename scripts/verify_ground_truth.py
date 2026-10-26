#!/usr/bin/env python3
"""scripts/verify_ground_truth.py — Ground-truth измерение сходимости роя через persistent ROS 2 подписчики.

В отличие от старого подхода (где на каждый опрос каждого борта делался отдельный `docker exec` + `rclpy.init()`),
данный скрипт запускает 5 ДОЛГОЖИВУЩИХ подписчиков внутри каждого контейнера drone-agent-{1..5}.
Подписчики стартуют ДО начала сценария и непрерывно слушают локальный топик `/swarm/local/state`.
Каждое сообщение мгновенно стримится в хост-оркестратор с точной временной меткой time.time().
Это полностью исключает накладные расходы на запуск процессов и discovery FastDDS из замера сходимости.
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, "scripts")
from stress_test import reset_stand, sh, dcx, N_DEFAULT  # noqa: E402

CONTAINER_PROBE_CODE = """
import rclpy, json, sys, time
from rclpy.node import Node
from std_msgs.msg import String

drone_id = int(sys.argv[1])
rclpy.init()
n = Node(f'gt_probe_{drone_id}')

def cb(m):
    try:
        st = json.loads(m.data)
        out = {
            'drone': drone_id,
            't': time.time(),
            'epoch': st.get('epoch'),
            'winners': st.get('winners', {}),
            'alive': st.get('alive', [])
        }
        sys.stdout.write(json.dumps(out) + '\\n')
        sys.stdout.flush()
    except Exception:
        pass

n.create_subscription(String, '/swarm/local/state', cb, 10)
try:
    rclpy.spin(n)
except Exception:
    pass
finally:
    try:
        n.destroy_node()
        rclpy.shutdown()
    except Exception:
        pass
"""


class GroundTruthHarness:
    def __init__(self, n=N_DEFAULT):
        self.n = n
        self.procs = {}
        self.threads = {}
        self.latest_states = {}
        self.lock = threading.Lock()
        self.running = False

    def start_probes(self):
        self.running = True
        for i in range(1, self.n + 1):
            service = f"drone-agent-{i}"
            cmd = [
                "docker", "compose", "exec", "-T", service,
                "/usr/local/bin/ros-run.sh", "python3", "-u", "-c",
                CONTAINER_PROBE_CODE, str(i)
            ]
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
            self.procs[i] = p
            t = threading.Thread(target=self._reader_thread, args=(i, p), daemon=True)
            self.threads[i] = t
            t.start()

        # Ожидаем, пока все 5 подписчиков пришлют хотя бы по одному сообщению (готовность DDS)
        print("Ожидание готовности persistent подписчиков во всех контейнерах...", flush=True)
        t0 = time.time()
        while time.time() - t0 < 25:
            with self.lock:
                if len(self.latest_states) == self.n:
                    print(f"Все {self.n} подписчиков активны и принимают сообщения (warmup: {round(time.time() - t0, 2)}s).", flush=True)
                    return True
            time.sleep(0.2)
        print(f"ВНИМАНИЕ: За 25с ответили только {len(self.latest_states)}/{self.n} бортов.", flush=True)
        return False

    def _reader_thread(self, drone_id, proc):
        for line in proc.stdout:
            if not self.running:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                with self.lock:
                    self.latest_states[drone_id] = msg
            except Exception:
                pass

    def get_snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.latest_states.items()}

    def stop_probes(self):
        self.running = False
        for p in self.procs.values():
            try:
                p.terminate()
            except Exception:
                pass

def ew_token():
    """Bearer для изменяющих методов ew-controller: EW_TOKEN из окружения или из .env в корне репозитория."""
    tok = os.environ.get("EW_TOKEN")
    if tok:
        return tok
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        for line in open(env, encoding="utf-8"):
            if line.startswith("EW_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""



def read_shm(service):
    r = dcx(service, "cat", "/dev/shm/swarm_state.json", timeout=2)
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return None


def run_merge_test(harness, run_idx, max_wait=40.0):
    print(f"\n=================== ПРОГОН {run_idx + 1} ===================", flush=True)
    sh(["./scripts/ewctl.sh", "reset"])
    time.sleep(2)

    # 1. Снимаем базовое состояние
    base_snapshot = harness.get_snapshot()
    base_epochs = {s["epoch"] for s in base_snapshot.values() if s.get("epoch") is not None}
    print(f"Базовое состояние: эпохи={base_epochs}", flush=True)

    # 2. Инъекция сетевого разделения L4: {1,2,3} | {4,5}
    groupA, groupB = [1, 2, 3], [4, 5]
    print("Включение сетевого разделения L4 ({1,2,3} | {4,5})...", flush=True)
    for a in groupA:
        sh(["curl", "-fsS", "-X", "POST", "-H", f"Authorization: Bearer {ew_token()}", "http://127.0.0.1:8080/v1/ew/mesh",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"level": "L4", "drones": [a], "peers": groupB})])
    for b in groupB:
        sh(["curl", "-fsS", "-X", "POST", "-H", f"Authorization: Bearer {ew_token()}", "http://127.0.0.1:8080/v1/ew/mesh",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"level": "L4", "drones": [b], "peers": groupA})])

    print("Разделение удерживается 20 секунд...", flush=True)
    time.sleep(20)

    # Состояние перед слиянием
    split_snapshot = harness.get_snapshot()
    epochs_split = {i: s.get("epoch") for i, s in split_snapshot.items()}
    print(f"Эпохи перед слиянием: {epochs_split}", flush=True)

    # 3. Снятие разделения (Merge)
    print("Снятие разделения (ewctl reset)... Засекаем время сходимости!", flush=True)
    t_heal = time.time()
    sh(["./scripts/ewctl.sh", "reset"])

    # 4. Непрерывное наблюдение сходимости через persistent ROS 2 подписчики
    t_converged_ros = None
    converged_epoch_ros = None
    t_converged_shm = None
    converged_epoch_shm = None

    t_end = t_heal + max_wait
    while time.time() < t_end:
        # А. Проверка ROS 2 ground-truth потока
        if t_converged_ros is None:
            snap = harness.get_snapshot()
            if len(snap) == harness.n:
                epochs = {s["epoch"] for s in snap.values() if s.get("epoch") is not None}
                seen, dup = {}, 0
                for s in snap.values():
                    for task, who in (s.get("winners") or {}).items():
                        if task in seen and seen[task] != who:
                            dup += 1
                        seen[task] = who
                fresh = all(s["t"] >= t_heal for s in snap.values())
                all_alive = all(len(s.get("alive", [])) == harness.n for s in snap.values())
                # Истинная сходимость: все 5 дронов взаимно видят друг друга alive, единая эпоха, 0 дубликатов задач
                if fresh and all_alive and len(epochs) == 1 and dup == 0:
                    t_converged_ros = time.time() - t_heal
                    converged_epoch_ros = list(epochs)[0]

        # Б. Проверка пути /dev/shm
        if t_converged_shm is None:
            shm_states = {}
            for i in range(1, harness.n + 1):
                st = read_shm(f"drone-agent-{i}")
                if st:
                    shm_states[i] = st
            if len(shm_states) == harness.n:
                epochs_shm = {s.get("epoch") for s in shm_states.values() if s.get("epoch") is not None}
                seen_shm, dup_shm = {}, 0
                for s in shm_states.values():
                    for task, who in (s.get("winners") or {}).items():
                        if task in seen_shm and seen_shm[task] != who:
                            dup_shm += 1
                        seen_shm[task] = who
                all_alive_shm = all(len(s.get("alive", [])) == harness.n for s in shm_states.values())
                if all_alive_shm and len(epochs_shm) == 1 and dup_shm == 0:
                    t_converged_shm = time.time() - t_heal
                    converged_epoch_shm = list(epochs_shm)[0]

        if t_converged_ros is not None and t_converged_shm is not None:
            break
        time.sleep(0.05)

    print(f"Результат прогона {run_idx + 1}:")
    print(f"  ROS 2 ground-truth (persistent subscriber): t = {round(t_converged_ros, 2) if t_converged_ros else 'TIMEOUT'}s, epoch = {converged_epoch_ros}")
    print(f"  /dev/shm snapshot:                          t = {round(t_converged_shm, 2) if t_converged_shm else 'TIMEOUT'}s, epoch = {converged_epoch_shm}")
    if t_converged_ros and t_converged_shm:
        delta = round(t_converged_ros - t_converged_shm, 3)
        print(f"  Разница (ROS2_ground_truth - SHM):           {delta:+.3f}s")
    else:
        delta = None

    return {
        "run": run_idx + 1,
        "t_ros_ground_truth": round(t_converged_ros, 2) if t_converged_ros else None,
        "epoch_ros": converged_epoch_ros,
        "t_shm": round(t_converged_shm, 2) if t_converged_shm else None,
        "epoch_shm": converged_epoch_shm,
        "delta": delta
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args()

    harness = GroundTruthHarness(N_DEFAULT)
    try:
        ok = harness.start_probes()
        if not ok:
            print("Не удалось запустить все persistent подписчики.", file=sys.stderr)
            sys.exit(1)

        results = []
        for i in range(a.runs):
            res = run_merge_test(harness, i)
            results.append(res)
            time.sleep(3)

        out_dir = ROOT / "logs" / "verify" / time.strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "ground_truth_merge.json").write_text(json.dumps(results, indent=2))

        print("\n" + "=" * 80)
        print("ИТОГИ СВЕРКИ GROUND TRUTH (PERSISTENT ROS 2 vs SHM)")
        print("=" * 80)
        print(f"{'Прогон':<8} | {'ROS 2 Ground Truth (с)':<24} | {'/dev/shm (с)':<15} | {'Delta (с)':<10}")
        print("-" * 80)
        for r in results:
            t_r = f"{r['t_ros_ground_truth']} (ep {r['epoch_ros']})" if r['t_ros_ground_truth'] else "TIMEOUT"
            t_s = f"{r['t_shm']} (ep {r['epoch_shm']})" if r['t_shm'] else "TIMEOUT"
            d = f"{r['delta']:+.3f}" if r['delta'] is not None else "N/A"
            print(f"{r['run']:<8} | {t_r:<24} | {t_s:<15} | {d:<10}")
        print("-" * 80)

        ros_times = [r["t_ros_ground_truth"] for r in results if r["t_ros_ground_truth"] is not None]
        shm_times = [r["t_shm"] for r in results if r["t_shm"] is not None]
        deltas = [r["delta"] for r in results if r["delta"] is not None]

        if ros_times:
            avg_ros = sum(ros_times) / len(ros_times)
            avg_shm = sum(shm_times) / len(shm_times) if shm_times else 0
            avg_delta = sum(deltas) / len(deltas) if deltas else 0
            print(f"Среднее время ROS 2 Ground Truth: {avg_ros:.2f}с")
            print(f"Среднее время /dev/shm:           {avg_shm:.2f}с")
            print(f"Среднее расхождение (ROS - SHM):  {avg_delta:+.3f}с")
            print("=" * 80)

    finally:
        harness.stop_probes()


if __name__ == "__main__":
    main()
