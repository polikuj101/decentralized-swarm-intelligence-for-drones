// Английский интерфейс для показа: фразы из index.html и app.js заменяются в тексте страницы, включая то, что app.js дорисовывает.
// По умолчанию английский; ?lang=ru возвращает русский. Не переведённое остаётся русским.
(() => {
  if (new URLSearchParams(location.search).get("lang") === "ru") return;
  const D = [
    ["Введите приказ: например, Разведать квадрат Браво, вскрыть позиции, при обнаружении целей — доразведка и зависание",
     "Enter an order, e.g.: Drones 1 and 2 search Delta and Foxtrot, the rest stay in Alpha"],
    ["Отправить приказ (Pre-Flight)", "Send order (Pre-Flight)"], ["Готов к приказу", "Ready for order"],
    ["Бортовой планировщик и события", "Onboard planner & events"], ["события роя", "swarm events"], ["автопрокрутка", "autoscroll"],
    ["Целей пока не зафиксировано (ожидание входа борта в режим DWELL над объектом)", "No targets recorded yet (waiting for a drone to enter DWELL over a target)"],
    ["Управление РЭБ", "EW control"], ["Борты:", "Drones:"], ["длительность, с", "duration, s"], ["до снятия", "until cleared"],
    ["Обрыв C2", "Cut C2"], ["Вернуть C2", "Restore C2"], ["GPS: применить", "GPS: apply"], ["Вернуть GPS", "Restore GPS"],
    ["drift/spoof: скорость", "drift/spoof: speed"], ["м/с,", "m/s,"], ["курс", "heading"], ["цель N", "target N"], ["Вернуть mesh", "Restore mesh"],
    ["Split: жёсткое разделение mesh (L4, 100% потерь между группами), авто-снятие через 120 с. Heal запускает таймер слияния.",
     "Split: hard mesh partition (L4, 100% loss between groups), auto-clear after 120 s. Heal starts the merge timer."],
    ["Сброс всей РЭБ", "Reset all EW"], ["Сценарий ▶", "Scenario ▶"], ["■ Стоп", "■ Stop"],
    ["эпоха", "epoch"], ["задач готово", "tasks done"], ["живых бортов", "drones alive"], ["нет телеметрии", "no telemetry"],
    ["ждёт scene.json", "waiting for scene.json"], ["все борты", "all drones"], ["сценарий", "scenario"],
    ["Скомпилированный Task Manifest: ", "Compiled Task Manifest: "], ["Зоны:", "Zones:"], ["При контакте:", "On contact:"],
    ["Инференс:", "Inference:"], ["Адресация бортов:", "Drone assignment:"], ["Разбор: зоны — ", "Parsing: zones — "],
    ["при контакте —", "on contact —"], ["время —", "time —"], ["из текста", "from text"], ["по умолчанию", "default"],
    [" · бортовая модель недоступна", " · onboard model unavailable"], [" · модель предлагала зоны", " · model suggested zones"],
    ["Исполняются: сектора, режим при контакте (SKIP — цели пропускаются, DWELL_ONLY — зависание без снимка, DWELL_RECON — со снимком), время зависания и возврат. Имена зон → сектора заданы в таблице, а не выводятся из карты; цели на карте известны заранее.",
     "Executed: sectors, on-contact mode (SKIP — targets ignored, DWELL_ONLY — hover without a photo, DWELL_RECON — with a photo), dwell time and return. Zone names map to sectors from a table, not from the map; targets on the map are known in advance."],
    ["Манифест принят координаторами всех бортов", "Manifest accepted by the coordinators of all drones"], ["НЕ принят координаторами:", "NOT accepted by coordinators:"],
    ["Компиляция", "Compiling"], ["бортов)...", "drones)..."], ["Ошибка сети:", "Network error:"], ["Ошибка:", "Error:"],
    ["Скомпилирован на", "Compiled on"], ["жду подтверждения координаторов…", "waiting for coordinator confirmation…"],
    ["Приказ передан бортам в", "Order sent to the drones at"], ["скомпилирован на", "compiled on"], ["бортах", "drones"],
    ["вымышленная местность, не реальный район", "fictional terrain, not a real place"], ["модель", "model"], [" с) |", " s) |"], [" мс", " ms"],
    ["Манифест не скомпилирован ни на одном борту", "Manifest not compiled on any drone"],
    ["ВНИМАНИЕ: борты скомпилировали РАЗНЫЕ манифесты", "WARNING: drones compiled DIFFERENT manifests"], ["варианта), принято ответов", "variants), replies accepted"],
    ["скомпилировали иначе,", "compiled differently,"], ["выровнены по большинству", "aligned to the majority"], ["НЕ выровнены", "NOT aligned"],
    ["норма", "normal"], ["Препятствие", "Obstacle"], ["Задача", "Task"], ["Фаза/", "Phase/"],
    ["Цель в кадре не обнаружена", "Target not detected in frame"], ["Обнаружен", "Detected"], ["Координаты:", "Coordinates:"],
    ["Источник:", "Source:"], ["Фото:", "Photo:"], ["ошибка", "error"], ["выполнено, ошибок у агентов:", "done, agent errors:"],
  ].sort((a, b) => b[0].length - a[0].length);
  const tr = (s) => { let o = s; for (const [a, b] of D) if (o.includes(a)) o = o.split(a).join(b); return o; };
  const fix = (n) => {
    if (n.nodeType === 3) { const t = tr(n.nodeValue); if (t !== n.nodeValue) n.nodeValue = t; return; }
    if (n.nodeType !== 1 || n.tagName === "SCRIPT" || n.tagName === "STYLE") return;
    for (const a of ["placeholder", "title", "aria-label"]) { const v = n.getAttribute(a); if (v) { const t = tr(v); if (t !== v) n.setAttribute(a, t); } }
    n.childNodes.forEach(fix);
  };
  fix(document.body);
  new MutationObserver((ms) => { for (const m of ms) { if (m.type === "characterData") fix(m.target); else m.addedNodes.forEach(fix); } })
    .observe(document.body, {childList: true, subtree: true, characterData: true});
})();
