import ast, json, sys, time, threading, urllib.request
REPO = "/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones"
src = open(REPO + "/docker/drone-agent/swarm/tasking.py", encoding="utf-8").read()
SYSTEM = [ast.literal_eval(n.value) for n in ast.parse(src).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SYSTEM"][0]
GRAMMAR = open(REPO + "/docker/drone-agent/grammar/manifest.gbnf", encoding="utf-8").read()
port = int(sys.argv[1])
def ask(prompt):
    p = f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    body = {"prompt": p, "grammar": GRAMMAR, "n_predict": 100, "temperature": 0.0, "top_k": 1, "seed": 42, "cache_prompt": True, "stop": ["<|im_end|>"]}
    t0 = time.monotonic()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/completion", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r: json.loads(r.read())
    return round(time.monotonic() - t0, 2)
order = "Look at Echo; a yes or no on contacts is all I need"
print("холодный 1-й вызов:", ask(order))
print("5 подряд, один приказ (кэш подсказки):", [ask(order) for _ in range(5)])
print("другой приказ после этого:", ask("Check Charlie and just tell me whether anything is there"))
res = []
def w(): res.append(ask("Survey Bravo and then head back to the launch point"))
ts = [threading.Thread(target=w) for _ in range(5)]
t0 = time.monotonic(); [t.start() for t in ts]; [t.join() for t in ts]
print("5 одновременных (как веер HUD), новый приказ: каждый", sorted(res), "всего", round(time.monotonic() - t0, 2))