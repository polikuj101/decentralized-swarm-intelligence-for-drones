.PHONY: all fetch-embedder build up down restart test-s1 test-hud bench isolation soak failover help

all: help

fetch-embedder:
	./scripts/fetch_embedder.sh

build:
	docker compose build drone-agent-1

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart drone-agent-1 drone-agent-2 drone-agent-3 drone-agent-4 drone-agent-5

test-s1:
	docker compose exec -T drone-agent-1 /usr/local/bin/ros-run.sh pytest -v -p no:launch_testing -p no:launch_ros -p no:anyio /opt/swarm/tests

# Тесты web-hud на хосте (pip install pytest fastapi httpx). cd нужен: корневой tests/ — симлинк, который pytest под Windows не обходит.
test-hud:
	cd docker/web-hud && python -m pytest tests -q -p no:cacheprovider

bench:
	./scripts/bench_decision.sh micro
	./scripts/bench_decision.sh live 60 after

isolation:
	./scripts/check_isolation.sh

soak:
	./scripts/soak_mem.sh 300

failover:
	./scripts/failover_test.sh 3

stress:
	./scripts/run_stress.sh all

scorecard:
	python3 ./scripts/scorecard.py $$(ls -td logs/stress/*/report.json | head -1)

help:
	@echo "Targets:"
	@echo "  fetch-embedder - Download ONNX embedding model"
	@echo "  build          - Build drone-agent-1"
	@echo "  up             - Start docker compose stack"
	@echo "  down           - Stop docker compose stack"
	@echo "  restart        - Restart drone-agents"
	@echo "  test-s1        - Run pytest inside drone-agent-1"
	@echo "  test-hud       - Run web-hud pytest on the host (convergence, guard, evidence ids)"
	@echo "  bench          - Run micro and live decision benchmarks"
	@echo "  isolation      - Check resource isolation and limits"
	@echo "  soak           - Run 300s memory leak soak test"
	@echo "  failover       - Run failover test on drone 3"
