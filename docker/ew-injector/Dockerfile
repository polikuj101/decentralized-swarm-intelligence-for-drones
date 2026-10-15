FROM python:3.11-slim
RUN pip install --no-cache-dir "fastapi==0.115.*" "uvicorn[standard]==0.30.*" \
      "httpx==0.27.*" "pyyaml==6.*" "pydantic==2.*"
WORKDIR /app
COPY controller.py /app/controller.py
# каталог scenarios монтируется томом (см. docker-compose.override.yml)
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"
CMD ["uvicorn", "controller:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
