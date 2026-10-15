"""swarm/embedder.py — ONNX Runtime (CPU) эмбеддер с Matryoshka-усечением. Не импортирует swarm.common."""
import os
import pathlib
import time

import numpy as np

PREFIX = "task: sentence similarity | query: "


class Embedder:
    def __init__(self, model_dir: str, dim: int = 128, max_len: int = 64, threads: int = 1):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        d = pathlib.Path(model_dir)
        self.model_file = os.environ.get("S1_EMBED_FILE", "model_quantized.onnx")
        so = ort.SessionOptions()
        so.intra_op_num_threads, so.inter_op_num_threads = threads, 1
        so.enable_cpu_mem_arena = False
        so.enable_mem_pattern = False
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(d / self.model_file), so, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.dim, self.max_len = dim, max_len
        self.in_names = {i.name for i in self.sess.get_inputs()}
        outs = [o.name for o in self.sess.get_outputs()]
        self.out = next((n for n in outs if "sentence_embedding" in n), outs[0])
        self.pooled = "sentence_embedding" in self.out
        self.tag = f"{self.model_file}:{dim}"

    def encode(self, text: str) -> np.ndarray:
        ids = self.tok.encode(PREFIX + text).ids[: self.max_len]
        feeds = {"input_ids": np.asarray([ids], dtype=np.int64),
                 "attention_mask": np.ones((1, len(ids)), dtype=np.int64)}
        feeds = {k: v for k, v in feeds.items() if k in self.in_names}
        v = self.sess.run([self.out], feeds)[0]
        v = v[0] if self.pooled else v[0].mean(axis=0)          # [768] либо усреднение last_hidden_state
        v = np.asarray(v, dtype=np.float32)[: self.dim]
        return v / (np.linalg.norm(v) + 1e-9)


def quantize_int8(v: np.ndarray, scale: float = 0.5) -> bytes:
    """для зарезервированного T_SVEC (п. 7.6): компоненты после L2-нормы лежат в ±scale"""
    return np.clip(np.round(v / scale * 127.0), -127, 127).astype(np.int8).tobytes()


def build_protos(emb: Embedder, path: str) -> None:
    from swarm.features import render
    from swarm.prototypes import PROTOS
    rows, tools = [], []
    for tool, specs in PROTOS.items():
        for spec in specs:
            rows.append(emb.encode(render(spec)))
            tools.append(tool)
    np.savez(path, P=np.stack(rows).astype(np.float32), tools=np.array(tools), tag=np.array(emb.tag))


def load_or_build_protos(emb: Embedder, path: str):
    try:
        z = np.load(path, allow_pickle=False)
        if str(z["tag"]) != emb.tag:
            raise ValueError("tag mismatch")
    except Exception:  # noqa: BLE001
        path = "/tmp/protos.npz"
        t0 = time.monotonic()
        build_protos(emb, path)
        print(f"protos rebuilt in {time.monotonic() - t0:.1f}s")
        z = np.load(path, allow_pickle=False)
    return z["P"], [str(t) for t in z["tools"]]
