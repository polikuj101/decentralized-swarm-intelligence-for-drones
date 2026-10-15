import json
import time
import urllib.request

LLM_URL = "http://127.0.0.1:8080"
prompt = "<|im_start|>system\nYou are a tactical planner.<|im_end|>\n<|im_start|>user\nChoose action.<|im_end|>\n<|im_start|>assistant\n"
body = {"prompt": prompt, "n_predict": 40, "temperature": 0.2}

latencies = []
for i in range(5):
    t0 = time.perf_counter()
    req = urllib.request.Request(
        f"{LLM_URL}/completion",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())
    dt = (time.perf_counter() - t0) * 1000.0
    latencies.append(dt)
    print(f"Call {i+1}: {dt:.1f} ms, tokens: {res.get('tokens_predicted')}")

print(f"Median baseline latency: {sorted(latencies)[len(latencies)//2]:.1f} ms")
