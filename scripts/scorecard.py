#!/usr/bin/env python3
"""scripts/scorecard.py <report.json> — Расчет Scorecard зрелости роя (1-10) по разделу 8.4."""
import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Использование: python3 scripts/scorecard.py <path/to/report.json>")
    sys.exit(1)

path = Path(sys.argv[1])
if not path.is_file():
    print(f"Файл не найден: {path}")
    sys.exit(1)

data = json.load(open(path))
results = {x["vector"]: x for x in data.get("results", [])}

CATEGORIES = {
    1: ("Устойчивость consensus к сетевому хаосу", 0.20),
    2: ("Отказоустойчивость к убыли бортов", 0.20),
    3: ("Безопасность навигации при сенсорном сбое", 0.20),
    4: ("Устойчивость ресурсов под нагрузкой", 0.20),
    5: ("Корректность разрешения коллизий", 0.20),
}


def score_vector(v, r_dict):
    x = r_dict.get(v, {})
    verdict = x.get("verdict", "ERROR")
    if verdict == "PASS":
        return 10
    elif verdict == "FAIL":
        return 5  # Восстановление с нарушением порога без ручного вмешательства
    else:
        return 1  # ERROR или ручное вмешательство


scores = {}
print("\n" + "=" * 80)
print("SCORECARD ЗРЕЛОСТИ РАСПРЕДЕЛЕННОГО СТЕКА РОЯ (РАЗДЕЛ 8.4)")
print("=" * 80)
print(f"{'Категория':<45} | {'Вес':<6} | {'Вердикт':<8} | {'Балл (1-10)':<10}")
print("-" * 80)

total_weighted = 0.0
for v, (cat_name, weight) in CATEGORIES.items():
    sc = score_vector(v, results)
    scores[v] = sc
    verdict = results.get(v, {}).get("verdict", "N/A")
    weighted = sc * weight
    total_weighted += weighted
    print(f"{cat_name:<45} | {int(weight*100)}%   | {verdict:<8} | {sc}/10")

print("-" * 80)
print(f"{'ИТОГОВАЯ ОЦЕНКА ЗРЕЛОСТИ РОЯ:':<54} | {total_weighted:.1f} / 10.0")
print("=" * 80 + "\n")
