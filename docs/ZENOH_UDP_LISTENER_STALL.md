# Заклинивание UDP-слушателя Zenoh: разбор и черновик отчёта в eclipse-zenoh

Дата: 2026-11-02. Не отправлено: отправка отчёта в публичный трекер — решение владельца.

## Разбор (по-русски)

Симптом на стенде: у борта навсегда `zenoh_peers: 0`. Правил помех нет, процессы живы, tcpdump показывает попытки соединения (UDP 32 байта на 7447) без ответов. У слушающего сокета `udp 172.31.0.N:7447` в координаторе очередь приёма 100-150 КБ, и она не разбирается.

Механизм, по исходникам eclipse-zenoh 1.10.1 (в `main` на 2026-11-02 так же), файл `io/zenoh-links/zenoh-link-udp/src/unicast.rs`:
1. Одна задача `accept_read_task` читает слушающий сокет за все входящие «соединения». Соединение здесь — пара адресов `(src, dst)` в таблице.
2. Каждую датаграмму она отдаёт соединению через `link.received(buff, n).await`, то есть `Mvar::put`. У ячейки ёмкость 1: `put` ждёт, пока читатель соединения заберёт предыдущую датаграмму.
3. Цикл держит сильную ссылку на соединение всё время ожидания. Если транспорт этого соединения закрыт (истёк lease, ошибка рукопожатия), а в ячейке уже лежит датаграмма, то больше никто не сделает `take`, и `put` не вернётся никогда.
4. Сокет больше не читается: очередь растёт, рукопожатия всех соседей не доходят, новые сессии не поднимаются.
5. Отмена слушателя не помогает. `del_listener` отменяет токен и ждёт задачу, а задача стоит внутри ветки `select!` и токен не проверяет. Порт 7447 остаётся занят до конца процесса.

Когда случается на стенде: только на борту, который сняли с `docker pause`, в первые секунды после снятия. Два прогона генератора по 10 минут: 9 и 4 случая примерно на 20 пауз в каждом, чаще при потерях (L1-L3), один раз при L0 (`logs/fd_gen3.log`, `logs/fd_gen4.log`, поле `mesh` в `logs/fd_runs/2026110[2]-20*/run.json`). Исходящие соединения заклинившего борта к соседям продолжают работать, пропадают только входящие, поэтому `zenoh_peers` не всегда 0. В отдельных опытах ни разу:
- 6 пауз без потерь (`logs/zenoh_repro_200551_A`);
- 4 минуты пачек L3 без пауз (`logs/zenoh_repro_201433_B`);
- 6 жёстких разрывов L4 (`logs/zenoh_repro_201959_C`).

Вероятная последовательность такая. После снятия паузы у борта сразу истекают lease всех сессий, транспорты закрываются. В очереди сокета в этот момент лежат датаграммы этих сессий, и цикл приёма раздаёт их закрывающимся соединениям. Не проверено: точный порядок событий; это потребует журнала `RUST_LOG=zenoh_link_udp=trace`. Минимальное воспроизведение без стенда, где мусорные датаграммы шлются с одного порта (`docker/drone-agent/tools/zenoh_udp_stall.py`), сбой **не** вызвало.

Что значит для настоящего борта: пауза контейнера — это зависание бортового компьютера на десятки секунд. После такого зависания на зашумлённом канале борт навсегда выпадал бы из mesh.

Обход в проекте (`swarm/mesh.py`, `swarm/mesh_cfg.py`):
- борт следит за очередью сокета 7447 по `/proc/net/udp`;
- если она не пуста и не убывает 15 с, координатор выходит, и supervisord его перезапускает;
- приказ берётся из тома состояния, задачи и эпоха приходят от соседей.

На стенде после каждого перезапуска борт возвращался в mesh. Выключение: `SWARM_MESH_STALL_RESTART=0`.

## Draft issue (English)

**Title:** UDP unicast listener stops reading forever when a link's reader goes away while a datagram is pending (`accept_read_task` blocks on `Mvar::put`)

**Version:** eclipse-zenoh (Python) 1.10.1; same code in `main` as of 2026-11-02.

**What happens.** A peer listening on `udp/<ip>:7447` ends up with zero sessions and never recovers. The listening socket's receive queue (`ss -uan`, `/proc/net/udp` `rx_queue`) fills up to 100-150 KB and is never drained. Other peers keep sending InitSyn to it with no reply. Restarting the session in-process does not help: the port stays bound. Only a process restart recovers.

**Where.** `io/zenoh-links/zenoh-link-udp/src/unicast.rs`, `accept_read_task`:
- every datagram is delivered with `link.received(buff, n).await`, i.e. `Mvar::put` on a capacity-1 `Mvar`, while holding a strong `Arc` to the unconnected link;
- if the transport that owns the link has stopped reading (closed after lease expiry or a failed handshake) and the `Mvar` is already full, `put` waits forever;
- the single accept loop serves all remote addresses, so one abandoned link blocks every peer;
- `del_listener` cancels the token and then awaits the task, but the task is parked inside the `select!` arm and never observes the cancellation, so the listener cannot be stopped either.

**How we hit it.** Five peers on a Linux bridge, UDP unicast, `lease` 4000 ms, `keep_alive` 4, receive-side packet loss 50-80 % (nftables). One peer process is frozen with `docker pause` for 15-55 s and then resumed. On the resumed peer, the listener stalled 9 times in 10 minutes. In a second 10-minute run it stalled 4 times out of 20 freezes, once with no packet loss. In isolated runs, 6 freezes with no loss, and loss without freezing (4 min), did not reproduce it. Outgoing sessions of the stalled peer keep working; only incoming ones are lost.

**Suggested fix.** Never block the accept loop on a single link:
- use a non-blocking `try_put`, or a short timeout, and drop the datagram if the link does not take it (UDP is lossy anyway); or
- give each link a bounded channel and drop when it is full, and remove the link from the table when its reader is gone.

Also make the loop observe the cancellation token while delivering, so `del_listener` can complete.

**Workaround we use.** Monitor the listening socket's `rx_queue`. If it is non-zero and not decreasing for 15 s, restart the process.
