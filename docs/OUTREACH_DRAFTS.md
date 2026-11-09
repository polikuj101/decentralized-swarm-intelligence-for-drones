# Черновики писем (2026-10-29)

Это черновики, не отправленные письма. Перед отправкой подставьте `[…]`: имя получателя, ваше имя и должность, название компании/продукта, ссылку на видео. Каждый получатель получает своё письмо с одной персональной фразой в начале (что именно у него вы прочитали или увидели). Рассылкой по списку это работать не будет.

## Что можно утверждать (всё проверено на стенде за последние дни)

- Симуляция: PX4 SITL и Gazebo Harmonic, 5 бортов. Не полёты.
- Координация без центрального узла: P2P-mesh (Zenoh), распределение задач аукционом (CBAA).
- Сценарий показа: глушение канала управления и GPS, потеря бортов, оставшиеся борта сами перераспределяют сектора и возвращаются.
- Модель на борту (0,5B) только советник в тяжёлых фазах (потеря половины роя и хуже); каждую команду проверяет детерминированный валидатор. Качество советов модели не измерено, не обещайте большего.
- Объём: ISR и координация. Поражающих функций нет.

## Чего утверждать нельзя

- Реальные полёты, тесты на железе, эмерджентный «роевой интеллект» как свойство, которое растёт с числом бортов (масштабирование не проверялось, 5 бортов).
- Сроки, цены, поставки в конкретные страны (экспортный режим не выяснен).
- Сравнения с конкурентами по цифрам.

---

## Письмо 1: OEM и оборонные интеграторы (английский)

**Subject:** Coordination layer for drone swarms that keeps working when links and GPS don't

Hi [Name],

[One sentence about them: e.g. "I read about your work on … and thought of a problem we are working on."]

We are building a coordination layer for small drone swarms (ISR and coordination only) that keeps working when the C2 link and GPS are jammed and when some of the drones are lost. There is no central node: the drones talk over a peer-to-peer mesh and re-allocate tasks among themselves.

In our simulation (PX4 SITL and Gazebo, 5 drones) we jam the C2 link and GPS, then remove drones; the survivors detect the losses, re-split the search sector and return home, and every command passes a rule-based validator. A 3-minute recording is here: [LINK].

This is a simulation, not flight test, and we are early. What I would like to learn from you is whether this fits how integrators like [Company] plan to handle degraded-comms operations, and what you would need to see next (hardware-in-the-loop, a specific autopilot, a specific payload) before a pilot makes sense.

Would a 20-minute call in the next two weeks work?

Best,
[Your name]
[Role, company, contact]

---

## Письмо 2: инвесторы (английский)

**Subject:** [Company]: swarm coordination that degrades gracefully under jamming (demo inside)

Hi [Name],

[One sentence: why them, e.g. "You backed … so I thought you might find this relevant."]

[Company] builds the coordination layer for small drone swarms operating without reliable comms or GPS. ISR and coordination only, no weapons. The decision logic runs on every drone, with no central node: a fast rule layer reacts to jamming and navigation loss, a task auction redistributes work when drones are lost, and a small on-board model is only an advisor in severe degradation.

Where we are: a working simulation of a 5-drone swarm (PX4 SITL and Gazebo) and a recorded demo: [LINK]. No flight tests yet, no customers yet. I am a solo founder, and I run the tests myself and track each failure in the open ([N] regression runs so far).

I am looking for [pre-seed amount or intro to integrators] to move from simulation to hardware-in-the-loop and a first pilot with an integrator.

Can I send you a short deck, or take 20 minutes of your time?

Best,
[Your name]

---

## Перед отправкой

1. Видео: запишите прогон сценария 05 (3,5 минуты) или каскад 06, с подписью «simulation». Живой показ только после ещё двух-трёх прогонов подряд.
2. Подставьте число регрессионных прогонов только если оно верное (не выдумывайте).
3. Проверьте страну получателя против экспортного режима, прежде чем обещать доступ к коду или сборке.
4. Не отправляйте ничего, что вы сами не можете показать на экране за минуту.
