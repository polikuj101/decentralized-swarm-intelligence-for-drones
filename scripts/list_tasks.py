import os
import sys

os.environ["MISSION_FILE"] = "docker/drone-agent/config/mission.json"
os.environ["SCENE_FILE"] = "docker/simulator-core/scene.json" if os.path.exists("docker/simulator-core/scene.json") else "docker/drone-agent/config/mission.json"
# Actually let's load scene from web-hud config
import urllib.request
import json

data = json.loads(urllib.request.urlopen("http://127.0.0.1:8888/api/config").read())
scene = data["scene"]
mission = data["mission"]

AREA = mission["search_area"]
COLS, ROWS = mission["grid"]["cols"], mission["grid"]["rows"]

cw = (AREA["x_max"] - AREA["x_min"]) / COLS
rh = (AREA["y_max"] - AREA["y_min"]) / ROWS

print("=== ВСЕ 9 ЗАДАЧ МИССИИ РОЯ ===")
print("-----------------------------------------------------------------------------------------")
print(f"{'ID':<3} | {'Имя':<4} | {'Тип':<8} | {'Координаты (X, Y)':<26} | {'Центр (X, Y)':<14} | {'Площадь / Радиус'}")
print("-----------------------------------------------------------------------------------------")

# Базовые сектора S0..S5
for r in range(ROWS):
    for c in range(COLS):
        p = r * COLS + c
        x0, y0 = AREA["x_min"] + c * cw, AREA["y_min"] + r * rh
        x1, y1 = x0 + cw, y0 + rh
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        area = abs((x1 - x0) * (y1 - y0))
        row_name = "Нижний" if r == 0 else "Верхний"
        col_name = "левый" if c == 0 else ("центр" if c == 1 else "правый")
        desc = f"{area:.0f} м² ({row_name} {col_name})"
        print(f"{p:<3} | S{p:<3} | SECTOR   | X: [{x0:4.1f}..{x1:4.1f}], Y: [{y0:5.1f}..{y1:4.1f}] | ({cx:4.1f}, {cy:5.1f})     | {desc}")

# Точечные цели T0..T2
for i, t in enumerate(scene.get("targets", [])[:3]):
    tid = 6 + i
    name = f"T{i}"
    x, y = t["x_m"], t["y_m"]
    rad = t.get("radius_m", 1.5)
    print(f"{tid:<3} | {name:<4} | TARGET   | Точка ({x:4.1f}, {y:5.1f})             | ({x:4.1f}, {y:5.1f})     | R = {rad}м (Цель {t.get('name', '')})")
print("-----------------------------------------------------------------------------------------")
