#!/usr/bin/env python3
"""Набор приказов без явных слов для order_rules: поля решает модель. usage: bench2.py <name> <think_off 0|1> <port>"""
import ast, json, re, sys, time, statistics, urllib.request, importlib.util
REPO = "/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones"
name, think_off, port = sys.argv[1], sys.argv[2] == "1", int(sys.argv[3])
spec = importlib.util.spec_from_file_location("order_rules", REPO + "/docker/drone-agent/swarm/order_rules.py")
rules_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(rules_mod)
src = open(REPO + "/docker/drone-agent/swarm/tasking.py", encoding="utf-8").read()
SYSTEM = [ast.literal_eval(n.value) for n in ast.parse(src).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SYSTEM"][0]
GRAMMAR = open(REPO + "/docker/drone-agent/grammar/manifest.gbnf", encoding="utf-8").read()
DEFAULTS = {"on_target": "DWELL_RECON", "dwell_s": 8, "rtb_on_complete": True}
R, O, S = "DWELL_RECON", "DWELL_ONLY", "SKIP"
# (приказ, ожидаемые поля). Поле указано, только если приказ определяет его по смыслу; слова правил (игнор/подтверд/зависн/остаться/вернуться…) не используются.
CASES = [
    ("Облети Эхо, нас интересует только рельеф, объекты на земле не нужны", {"on_target": S}),
    ("Fly over Delta, we do not care about anything you spot there", {"on_target": S}),
    ("Прочеши Фокстрот, обнаруженное нас не интересует", {"on_target": S}),
    ("Transit through Alpha; no need to stop at any contact", {"on_target": S}),
    ("Альфа: если кто-то там есть, достаточно отметить сам факт, без остановки и без фото", {"on_target": O}),
    ("Check Charlie and just tell me whether anything is there, no photos needed", {"on_target": O}),
    ("Дельта — нужен лишь факт наличия целей", {"on_target": O}),
    ("Look at Echo; a yes or no on contacts is all I need", {"on_target": O}),
    ("Осмотри Браво и сними всё, что найдёшь", {"on_target": R}),
    ("Search Foxtrot and get a closer look at each contact", {"on_target": R}),
    ("Альфа и Чарли: разберись детально с каждой находкой", {"on_target": R}),
    ("Check Delta; take pictures of whatever turns up", {"on_target": R}),
    ("Scout Echo, pause for a 10-second look at any contact", {"on_target": R, "dwell_s": 10}),
    ("Альфа: 12-секундная пауза над каждой находкой", {"on_target": R, "dwell_s": 12}),
    ("Search Alpha; park yourselves at the final waypoint, do not fly back", {"rtb_on_complete": False}),
    ("Обследуй Эхо, после этого приземляйтесь прямо на поле", {"rtb_on_complete": False}),
    ("Check Foxtrot and wait there for further orders", {"rtb_on_complete": False}),
    ("Обследуй Дельту и ждите дальнейших указаний там же", {"rtb_on_complete": False}),
    ("Survey Bravo and then head back to the launch point", {"rtb_on_complete": True}),
    ("Альфа, потом обратно к точке старта", {"rtb_on_complete": True}),
    ("Take a look at Foxtrot", {"dwell_s": 8, "rtb_on_complete": True}),
    ("Загляни в Дельту", {"dwell_s": 8, "rtb_on_complete": True}),
    ("Alpha and Echo please", {"dwell_s": 8, "rtb_on_complete": True}),
    ("Проверь Чарли и Браво", {"dwell_s": 8, "rtb_on_complete": True}),
]
def ask(prompt):
    p = f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n{prompt.strip()}<|im_end|>\n<|im_start|>assistant\n" + ("<think>\n\n</think>\n\n" if think_off else "")
    body = {"prompt": p, "grammar": GRAMMAR, "n_predict": 100, "temperature": 0.0, "top_k": 1, "seed": 42, "cache_prompt": True, "stop": ["<|im_end|>"]}
    t0 = time.monotonic()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/completion", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = json.loads(r.read())["content"]
    return json.loads(raw), (time.monotonic() - t0) * 1000
ask("Check Alpha")
tot, wrong, lat, caught = {}, [], [], []
allok = 0
bylang = {'en': [0, 0], 'ru': [0, 0]}
for prompt, exp in CASES:
    rules = rules_mod.parse(prompt)
    leaked = [k for k in exp if rules.get(k if k != "dwell_s" else "dwell_s") is not None]
    if leaked: caught.append((prompt[:50], leaked))
    m, ms = ask(prompt); lat.append(ms)
    fields, _src = rules_mod.merge(rules, m)
    for k, v in DEFAULTS.items(): fields.setdefault(k, v)
    ok = {k: fields[k] == v for k, v in exp.items() if k not in [x for x in leaked]}
    for k, v in ok.items():
        tot.setdefault(k, [0, 0]); tot[k][0] += v; tot[k][1] += 1
    lang = 'ru' if re.search('[а-яё]', prompt.lower()) else 'en'
    bylang[lang][1] += 1
    if ok and all(ok.values()): allok += 1; bylang[lang][0] += 1
    else: wrong.append((prompt[:55], {k: fields[k] for k, v in ok.items() if not v}))
n = len(CASES)
print(name, json.dumps({"pipeline_all_ok": f"{allok}/{n}", "english": f"{bylang['en'][0]}/{bylang['en'][1]}", "russian": f"{bylang['ru'][0]}/{bylang['ru'][1]}", "fields": {k: f"{a}/{b}" for k, (a, b) in tot.items()},
      "lat_p50_ms": round(statistics.median(lat)), "lat_max_ms": round(max(lat)), "rules_caught_expected_field": caught}, ensure_ascii=False))
for w in wrong: print("   ✗", w)