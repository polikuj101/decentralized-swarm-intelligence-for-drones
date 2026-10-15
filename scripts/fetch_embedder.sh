#!/usr/bin/env bash
# fetch_embedder.sh — ONNX-эмбеддер и токенизатор -> docker/drone-agent/models/embed/. Идемпотентен.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${S1_EMBED_REPO:-onnx-community/embeddinggemma-300m-ONNX}"
FILE="${S1_EMBED_FILE:-model_quantized.onnx}"
DEST=docker/drone-agent/models/embed
mkdir -p "$DEST"
[ -s "$DEST/$FILE" ] && [ -s "$DEST/tokenizer.json" ] && { echo "уже скачано: $DEST/$FILE"; exit 0; }
python3 -m pip install --user -q "huggingface_hub>=0.24"
python3 - "$REPO" "$FILE" "$DEST" <<'PY'
import pathlib
import shutil
import sys
from huggingface_hub import snapshot_download

repo, f, dest = sys.argv[1:4]
tmp = pathlib.Path(dest) / "_dl"
snapshot_download(
    repo_id=repo,
    local_dir=str(tmp),
    allow_patterns=[f"onnx/{f}", f"onnx/{f}_data*", "tokenizer.json", "tokenizer_config.json"],
)
for p in (tmp / "onnx").glob(f"{f}*"):
    shutil.copy(p, dest)
shutil.copy(tmp / "tokenizer.json", dest)
shutil.rmtree(tmp, ignore_errors=True)
PY
ls -lh "$DEST"
test -s "$DEST/$FILE" || { echo "FAIL: $FILE не скачан (проверьте имя файла в репозитории и HF_TOKEN)"; exit 1; }
