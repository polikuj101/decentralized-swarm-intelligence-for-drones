# Черновик заявки в YC (2026-11-02)

Это черновик, не отправленная заявка. Ответы на английском, как в форме YC; пояснения к каждому ответу на русском.
`[…]` заполняете вы: этих фактов в проекте нет, и выдумывать их нельзя.
Все цифры взяты из `docs/HANDOFF_OPEN_PROBLEMS.md`, `docs/COMPETITORS.md` и дизайн-дока от 2026-10-24. Это **симуляция**: в заявке так и написано.
Перед отправкой повторите замер слияния (6,27 с, 0 дублей): он из дизайн-дока, а после правок mesh 28-29 октября его не перемеряли.

Главная слабость заявки: ответы в разделе Progress о пользователях и выручке сейчас «нет». Каждый разговор с OEM до подачи усиливает заявку сильнее, чем любая правка текста ниже.

---

## Founders

**Who writes code, or does other technical work on your product?**

> I do. I design the system, write the specs, run every experiment and verify results against simulator ground truth. Much of the implementation code is AI-generated from my specs; I review it and treat it as unverified until it passes on the live test bench.

*Пояснение: YC спросит прямо, лучше сказать первым. Сильная сторона здесь — проверка, а не набор кода: сверка с позами Gazebo, регрессии, разбор ложных гипотез.*

**Founder background:** `[образование, опыт, что вы строили раньше, ссылки: GitHub, LinkedIn]`

**Are you looking for a cofounder?** `[да/нет]`

*Пояснение: если ищете, напишите, кого именно: человек с продажами в оборонку или OEM, который сам приведёт первых покупателей.*

---

## Company

**Company name:** `[…]`

**Describe what your company does in 50 characters or less.**

> Coordination layer for drone groups under jamming

*Запасной вариант (42 знака): «Drone groups that keep working when jammed».*

**Company URL / demo video:** `[ссылка]`

*Пояснение: 90-секундная запись сценария 05 или каскада 07 с подписью «simulation». Показать: глушение связи и GPS → потеря бортов → выжившие сами делят сектор → возврат.*

**What is your company going to make?**

> Software that runs on every drone in a group and keeps the group doing its job when the operator link and GPS are jammed and drones are lost.
>
> Today most small military and industrial drones are flown one pilot per aircraft. When the link is jammed, that aircraft's task is lost. We let a group of drones split an area among themselves and re-split it when one drops out, with no central node: drones talk peer-to-peer over a mesh (signed binary frames of 64 bytes or less) and allocate tasks with a consensus auction. Camera-based navigation holds position without GPS. A fast rule layer reacts to jamming and navigation loss; a small on-board model only advises in severe losses, and every command passes a deterministic validator. The operator gives orders in plain language ("D1 and D2: delta, foxtrot; the rest: alpha"), which are parsed on board into typed tasks.
>
> Scope is surveillance and coordination only, no weapons functions. We plan to sell it to drone makers as a per-aircraft license on top of PX4 and ROS 2.

---

## Progress

**How far along are you?**

> A working 5-drone simulation (PX4 autopilot software-in-the-loop + Gazebo), seven scenarios: C2 link cut, GPS denial, mesh degradation, full electronic warfare, and cascading drone losses. Results are checked against simulator ground truth, not our own UI:
> - GPS denied on all 5 drones for ~180 s: max position error 0.5–3.3 m per drone; navigation stayed valid (one drone dropped it for 4 frames).
> - After a network split and heal the group re-converges in 6.27 s with 0 duplicate task assignments in chaos tests.
> - A lost drone is detected by all peers in ~3.3 s on a clean link; survivors re-split its sector in ~9 s.
> - At 50% packet loss we cut false "drone dead" alarms from 152 to 37 per 5 minutes. Still not zero; this is the main open problem.
>
> Not done yet: no hardware-in-the-loop, no flights, no customers. Next: HIL on a real flight controller, then a 2–3 aircraft flight.

*Пояснение: открытая проблема с ложными DEAD сказана прямо. Это лучше, чем если её найдёт партнёр на интервью, и показывает, что вы меряете.*

**How long have each of you been working on this? How much of that has been full-time?**

> `[с какого месяца, сколько часов в неделю]`

*Пояснение: в git первый коммит 2026-10-15 («Initial commit before System 1»), работа начата раньше. Укажите настоящую дату начала.*

**What tech stack are you using, or planning to use, to build this product? Include AI models and AI coding tools you use.**

> On each drone: Python 3 agent on ROS 2 Humble, talking to the PX4 v1.15 autopilot over MAVLink and the Micro XRCE-DDS bridge. Drone-to-drone mesh on Eclipse Zenoh with our own binary protocol (frames of 64 bytes or less, HMAC-signed). Task allocation: consensus auction (CBAA). GPS-free navigation: our own camera optical flow plus rangefinders, fused in the PX4 EKF.
> On-board decisions: the fast layer is a table of deterministic rules (sub-millisecond, every rule numbered and shown to the operator). Experimental: a small quantized sentence-embedding model on ONNX Runtime (CPU) re-ranks the actions the rules allow by similarity to reference situations. Qwen2.5-0.5B-Instruct via llama.cpp is a slow advisor in severe losses, and every command it proposes passes a deterministic validator. No cloud calls in flight.
> Simulation and test: PX4 SITL + Gazebo Harmonic, 5 drones in Docker on WSL2; jamming emulated with nftables; FastAPI web HUD for the operator; pytest (256 tests) plus scripted regression runs checked against Gazebo ground truth.
> AI coding tools: Claude for design, specs and reviews (Claude Code), Google Antigravity (Gemini) for most of the implementation.
> Planned: hardware-in-the-loop on a Pixhawk-class flight controller with a companion computer, then a 2–3 aircraft flight.

*Пояснение: решения System 1 принимают правила (`swarm/rules.py`); эмбеддинг только переранжирует уже разрешённые правилами действия (`swarm/matcher.py`) и по перемеру 2026-11-02 на живом стенде не повлиял ни на одно решение (кодирование 5,4-5,8 с под нагрузкой, все решения взяты правилами), поэтому он помечен как experimental. Модель: EmbeddingGemma-300m int8 (`scripts/fetch_embedder.sh`). Если ROS 2 и XRCE для вас не главное, первый абзац можно сократить.*

**Are people using your product?** No.

**Do you have revenue?** No.

**Customer conversations so far:**

> `[число разговоров с OEM и интеграторами, одна-две дословные цитаты о последнем случае потери связи, любые обязательства: созвон с инженером, плата для HIL, LOI]`

*Пояснение: сейчас 0. Сценарий разговора и порог подтверждения есть в `docs/MOM_TEST_DISCOVERY.md`, письма в `docs/OUTREACH_DRAFTS.md`. Если до подачи успеете хотя бы 5 разговоров, перенесите этот пункт в начало раздела.*

---

## Idea

**Why did you pick this idea to work on?**

> `[личная история: почему вы, что вы видели сами, связь с регионом или отраслью]`
>
> The war in Ukraine made jamming the default, not the exception. Drones are cheap and mass-produced now; what does not scale is pilots. One pilot per aircraft breaks the moment the link is jammed, and the US Replicator program showed that the hard part is integration and reliability, not the swarm algorithm itself.

*Пояснение: вторая часть — общий контекст, первая важнее. YC ищет, почему именно вы.*

**What's new about what you're making? / What substitutes do people resort to?**

> Substitutes today: one pilot per drone, fiber-optic drones, relay drones, preloaded routes, or each drone maker writing its own coordination in-house. We are a vendor-neutral layer that keeps the group's tasks consistent when the network splits and heals, and we measure that property directly (time to re-converge, duplicate assignments, false failure alarms) instead of just claiming it.

**Who are your competitors?**

> Palladyne AI (SwarmOS, decentralized coordination, licensed per drone), Auterion (Nemyx, operator-directed swarms on AuterionOS), Shield AI (Hivemind), Anduril (Lattice for Mission Autonomy). The biggest real competitor is the in-house team at each drone maker.
>
> None of them publish how their coordination behaves under partial packet loss or after a network split. We intend to compete on that measured reliability, and on staying out of strike functions, which keeps us usable for civilian and export-restricted customers.

**What do you understand about your business that other companies just don't get?**

> Swarm failures under jamming are mostly not about the task-allocation algorithm. They are about failure detection. On a lossy link, surviving drones wrongly declare each other dead, and every false "dead" reshuffles tasks for the whole group, which looks like chaos from the ground. We found that each false failure was also spreading to every peer, and that heartbeat timing and a fixed death threshold caused most of it. Fixing propagation, heartbeat rate and making the threshold adapt to measured link gaps cut false alarms at 50% loss from 152 to 37 per 5 minutes. Vendors talk about algorithms; buyers lose aircraft to false failovers.
>
> We also learned that the test bench itself lies: dropping packets at the sender returns a send error that makes the mesh drop the whole link, so we spent days chasing a "swarm weakness" that was our emulator. We now record packets before forming hypotheses.

*Пояснение: это самый сильный ответ в заявке, потому что он из ваших данных. Цифры из `docs/HANDOFF_OPEN_PROBLEMS.md`, раздел «Новое, замечено по ходу».*

**How do or will you make money? How much could you make?**

> Per-aircraft license sold to drone makers, plus paid integration (NRE) for each new airframe. The closest public benchmark is Palladyne, which says it licenses at about 10% of drone price, typically one-time per drone.
>
> `[расчёт снизу вверх: N аппаратов в год, работающих группами, × цена лицензии. Опорная точка: контракт Auterion около €90 млн на 50 000 комплектов, т.е. около €1 800 на борт]`

*Пояснение: цифры рынка из отчётов (`docs/MARKET_ANALYSIS_SWARM.md`) расходятся в разы, первичные источники не проверены. В заявку лучше расчёт снизу вверх с вашими допущениями, чем «рынок $20 млрд».*

**Which category best applies?** Defense / Drones / Robotics.

**Other ideas you considered:** `[…]`

*Пояснение: можно назвать запасные сегменты из Mom Test: картография шахт и тоннелей без GPS, поиск и спасение.*

---

## Equity and location

**Where do you live now, and where would the company be based after YC?**

> `[город, Казахстан] / [где будет компания]`

**Have you formed a legal entity?** `[нет / страна]`

*Пояснение: YC обычно просит Delaware C-corp. Для оборонки это ещё и вопрос FOCI и экспортного контроля: решайте с юристом до интервью, не после.*

**Who will buy it, and what rules allow them to?** *(в форме такого вопроса нет, но на интервью он будет; ответ лучше вписать в «Anything else»)*

> `[целевой рынок первого года: Украина / Европа / Казахстан / гражданские операторы] — [что вы выяснили об экспортном режиме и требованиях к поставщику]`

*Пояснение: это главный риск заявки (допущение A14). Честный ответ «первые покупатели — гражданские операторы и OEM двойного назначения в [стране], ITAR-рынок США не цель первого года» сильнее, чем молчание.*

---

## Curious

**Tell us about the time you most successfully hacked some non-computer system to your advantage.** `[ваша история]`

**Tell us something surprising or amusing that you've discovered.**

> Our drone flew off 460 m into nowhere because the sky was too uniform. The camera-based navigation compares consecutive frames; past the edge of the textured ground, a featureless sky produced a perfectly "confident" match of zero motion, so the drone believed it was standing still while it accelerated away. Now tiles without texture don't get a vote.

*Пояснение: случай из `docs/HANDOFF_OPEN_PROBLEMS.md`, раздел 1 («Улёт за краем пола»).*

**How did you hear about YC?** `[…]`

---

## Перед отправкой

1. Перемерить слияние после разрыва (6,27 с, 0 дублей) на текущем коде; если число изменилось, заменить его везде.
2. Записать демо-видео (90 с, подпись «simulation») и видео основателя (1 минута, YC его требует): кто вы, что делаете, почему вы.
3. Провести хотя бы 5 разговоров по `docs/MOM_TEST_DISCOVERY.md` и вписать результаты в Progress.
4. Заполнить все `[…]`. Пустые места и общие слова в «Why did you pick this idea» видны сразу.
5. Проверить дедлайн текущего набора на ycombinator.com/apply.

## Вопросы на интервью: подготовить ответы

- Это вообще летает? (План HIL и облёта со сроками.)
- Кто первый покупатель, и может ли он по закону купить у вас? (Страна, экспортный режим.)
- Почему OEM не напишет это сам за полгода? (Ответ из разговоров, вопрос 12а.)
- Чем вы лучше Palladyne, у которого уже есть контракты и патент? (Измеренная надёжность и свобода использования: проверить патент с юристом.)
- Почему вы один, и кто будет продавать?
