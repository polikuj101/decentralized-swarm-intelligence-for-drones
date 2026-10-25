FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "fastapi==0.115.*" "uvicorn[standard]==0.30.*" "httpx==0.27.*" \
      "pymavlink==2.4.*" "pydantic==2.*"
WORKDIR /app
COPY static/ /app/static/
# Leaflet вендорится при сборке (на runtime интернет не нужен). Используются только векторные слои, картинки Leaflet не нужны.
RUN mkdir -p static/vendor \
 && (test -f static/vendor/leaflet.js || curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/leaflet.js -o static/vendor/leaflet.js) \
 && (test -f static/vendor/leaflet.css || curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/leaflet.css -o static/vendor/leaflet.css)
COPY main.py convergence.py order_consensus.py /app/
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
