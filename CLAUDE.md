# Swarm of Drones: что нужно знать до первой правки

Статус и открытые проблемы: **только** `docs/HANDOFF_OPEN_PROBLEMS.md`. `SPEC_*.txt`, `AUDIT_ANTIGRAVITY.md` и старые сводки исторические, им не верить без проверки по коду.
Инструкция по HUD и System 1/2: `docs/HUD_MANUAL.md`.

## Стенд
- WSL2 Ubuntu-22.04 + Docker, лимит памяти 10 ГБ (`.wslconfig`). WSL выключается, когда нет сессии: на время работы держать `wsl.exe -d Ubuntu-22.04 -- sleep infinity`.
- Цепочка compose лежит в `.env` (шаблон `.env.example`), в `docker-compose.yml` её не видно: агенты описаны в `docker-compose.swarm.yml`.
- Подъём: `scripts/swarm_fresh.sh` (сначала PX4, потом агенты). Потом **приказ в течение `MANIFEST_WAIT_S`**: пока приказа нет, координаторы не публикуют состояние, и тесты видят «0 событий».
- Демо: `DEMO_AUTOPILOT=1 scripts/run_demo.sh` (переменные `DEMO_SCENARIO`, `DEMO_KILL_DRONES`, `DEMO_KILL_AT_S`).
- Нагрузка: load ~13 на 16 ядрах, пороги по времени (`T_DEAD`, таймаут LLM) зависят от неё. В отчёте опыта писать load.

## Что вшито в образ, что смонтировано
- Смонтировано (`docker-compose.yml`, `x-agent`): `swarm/`, `ew/ew_agent.py`, `grammar/`, `supervisord.conf`. После правки перезапустить контейнер или `supervisorctl restart <процесс>`.
- Вшито: `tests/`, `ew-injector`, остальное из Dockerfile. Правка `tests/` на хосте в контейнер не попадает до `docker compose build drone-agent-1`.
- Состояние (приказ) живёт в томах `agent-N-state` и переживает перезапуск.

## Тесты
- `make test-host` (или `cd docker/drone-agent && PYTHONPATH=. python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_injection.py`): 247 тестов на хосте без ROS, WSL не нужен.
- `make test-s1`: в контейнере при живом стенде, включает `test_injection.py` (в общем прогоне падает, отдельно проходит).
- `make test-hud`: HUD на хосте.
- Корневые `tests` и `tools` — симлинки, под Windows pytest из корня их не видит.

## Ловушки (каждая стоила часов)
- **Сначала `tcpdump`, потом гипотезы.** Три неверные гипотезы про обрыв mesh опровергла бы одна минута съёмки (`docker exec <агент> tcpdump -i <iface> -nn -q udp port 7447`). Номер интерфейса mesh меняется после пересоздания контейнера.
- Эмуляция помех на WSL2 идёт через nftables (нет netem): задержка и коррелированные потери не воспроизводятся. Частичные потери режутся на **приёме**; drop на хуке `output` возвращает отправителю ошибку `sendto`, и Zenoh рвёт линк. Жёсткий обрыв L4 пока на `output`, поэтому восстановление включает переподключение Zenoh.
- Пороги `regress_*.py` подбирались под другую эмуляцию; запись эмуляции в отчёт: `ewctl.sh state`.
- Zenoh `keep_alive` — число keepalive за `lease`, а не миллисекунды (`swarm/mesh_cfg.py`, тест).
- Один прогон не вывод: повторять и сравнивать с контролем.
- Позиции в HUD не доказательство полёта: сверять с Gazebo (`docs/gz_leak/pose_log.py`, `vio_eval.py`, `docs/nav_runs/exp_demo.sh`).
- Файлы с CRLF и смешанными окончаниями: править через Edit или Python с `newline=""`, `.sh` для WSL писать с LF. Из Git Bash `grep $'\r'` врёт.
- Кавычки в `wsl.exe -- bash -c` ломаются: писать скрипт в файл (LF) и запускать `wsl.exe -- bash /mnt/c/...`.
- Прототипы System 1 (`swarm/prototypes.py`) кэшируются при сборке образа и сверяются по тегу модели: после правки прототипов пересобрать образ.
- `T_SUSPECT` задан и в `coordinator.py`, и в `features.py`; при правке менять оба.

## Границы
Только ISR и координация. Поражающие функции вне объёма. Коммиты в `feat/system1`, не пушить без запроса.
