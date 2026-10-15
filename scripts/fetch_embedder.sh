#!/usr/bin/env bash
# fetch_embedder.sh — ONNX-эмбеддер и токенизатор -> docker/drone-agent/models/embed/. Идемпотентен.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${S1_EMBED_REPO:-onnx-community/embeddinggemma-300m-ONNX}"
FILE="${S1_EMBED_FILE:-model_quantized.onnx}"
DEST=docker/drone-agent/models/embed
mkdir -p "$DEST"
if [ -s "$DEST/$FILE" ] && [ -s "$DEST/tokenizer.json" ]; then
  echo "уже скачано: $DEST/$FILE"
  exit 0
fi

download_hf() {
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
}

download_curl() {
  echo "Загрузка через curl напрямую из HuggingFace: $REPO..."
  BASE_URL="https://huggingface.co/${REPO}/resolve/main"
  
  echo "Скачивание onnx/${FILE}..."
  curl -L --fail -o "${DEST}/${FILE}" "${BASE_URL}/onnx/${FILE}"
  
  if curl -L -s -I "${BASE_URL}/onnx/${FILE}_data" | grep -q "200\|302"; then
    echo "Скачивание onnx/${FILE}_data..."
    curl -L --fail -o "${DEST}/${FILE}_data" "${BASE_URL}/onnx/${FILE}_data"
  fi

  echo "Скачивание tokenizer.json..."
  curl -L --fail -o "${DEST}/tokenizer.json" "${BASE_URL}/tokenizer.json"
}

if command -v python3 >/dev/null 2>&1 && python3 -m pip --version >/dev/null 2>&1; then
  download_hf || download_curl
else
  download_curl
fi

ls -lh "$DEST"
test -s "$DEST/$FILE" || { echo "FAIL: $FILE не скачан (проверьте имя файла в репозитории и HF_TOKEN)"; exit 1; }
