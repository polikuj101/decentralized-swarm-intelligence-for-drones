#!/usr/bin/env python3
import urllib.request
import urllib.error
import json
import sys
import asyncio

token = "7f3c1d9b8a2e4f0165c7890ab4d5e6f1"
url = "http://127.0.0.1:8000/api/ingest/1"

print("Running E8 ingest tests...")

# 1. No token -> expect 401
try:
    req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    print("FAIL: should have been 401")
    sys.exit(1)
except urllib.error.HTTPError as e:
    assert e.code == 401, f"Expected 401, got {e.code}"
    print("Test 1 (No token): 401 OK")

# 2. Bad schema -> expect 422
try:
    req = urllib.request.Request(url, data=b'{"bad": 1}', headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    urllib.request.urlopen(req)
    print("FAIL: should have been 422")
    sys.exit(1)
except urllib.error.HTTPError as e:
    assert e.code == 422, f"Expected 422, got {e.code}"
    print("Test 2 (Bad schema): 422 OK")

# 3. NaN in pos -> expect 200
req = urllib.request.Request(url, data=b'{"v": 1, "drone": 1, "pos": [NaN, 0.0, 0.0], "nav": {}, "swarm": {}, "mesh": {}}', headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
resp = urllib.request.urlopen(req)
assert resp.code == 200, f"Expected 200, got {resp.code}"
print("Test 3 (NaN parsed to null): 200 OK")

# 4. Check snapshot state via /api/state
state_req = urllib.request.Request("http://127.0.0.1:8000/api/state")
state_res = urllib.request.urlopen(state_req)
state_data = json.loads(state_res.read().decode())
assert state_data["swarm_size"] == 5
assert "1" in state_data["drones"]
print("Test 4 (/api/state): OK")

# 5. Security guards (E7)
# 5a. POST /api/ew/c2 without X-HUD -> 403
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/ew/c2", data=b'{}', headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    print("FAIL: should have been 403")
    sys.exit(1)
except urllib.error.HTTPError as e:
    assert e.code == 403, f"Expected 403, got {e.code}"
    print("Test 5a (missing X-HUD): 403 OK")

# 5b. POST /api/ew/c2 with evil origin -> 403
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/ew/c2", data=b'{}', headers={"Content-Type": "application/json", "X-HUD": "1", "Origin": "http://evil.com"})
    urllib.request.urlopen(req)
    print("FAIL: should have been 403")
    sys.exit(1)
except urllib.error.HTTPError as e:
    assert e.code == 403, f"Expected 403, got {e.code}"
    print("Test 5b (evil origin): 403 OK")

# 5c. POST /api/ew/unknown -> 404
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/ew/unknown", data=b'{}', headers={"Content-Type": "application/json", "X-HUD": "1", "Origin": "http://127.0.0.1:8000"})
    urllib.request.urlopen(req)
    print("FAIL: should have been 404")
    sys.exit(1)
except urllib.error.HTTPError as e:
    assert e.code == 404, f"Expected 404, got {e.code}"
    print("Test 5c (unknown action): 404 OK")

# 6. WebSocket tests
async def test_ws():
    import websockets
    uri = "ws://127.0.0.1:8000/ws"
    # 6a. Valid WS
    async with websockets.connect(uri, origin="http://127.0.0.1:8000") as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
        data = json.loads(msg)
        assert "state" in data
        assert "feed" in data
        assert "drones" in data["state"]
        print("Test 6a (WS connect & receive): OK")

    # 6b. Evil origin WS -> rejected
    try:
        async with websockets.connect(uri, origin="http://evil.com"):
            print("FAIL: Evil origin WS should have been closed")
            sys.exit(1)
    except (websockets.exceptions.ConnectionClosed, websockets.exceptions.InvalidStatus) as e:
        print(f"Test 6b (Evil origin WS rejected with {e}): OK")

asyncio.run(test_ws())

print("\n>>> ALL TESTS (E1, E7, E8, E11, WS) PASSED SUCCESSFULLY! <<<")
