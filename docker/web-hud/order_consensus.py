"""order_consensus.py — единый манифест для всех бортов.

Каждый борт компилирует приказ своим Qwen-0.5B; живой прогон показал, что на одном промпте борта могут отличаться (rtb True/False).
Параметры приказа исполняются на каждом борту отдельно, поэтому HUD выбирает манифест большинства и раздаёт его остальным."""
import json

FIELDS = ("active_sectors", "on_target", "dwell_s", "rtb_on_complete")


def signature(manifest: dict) -> str:
    assign = sorted((k, sorted(v)) for k, v in (manifest.get("assignments") or {}).items())
    return json.dumps([sorted(manifest.get("active_sectors") or []), manifest.get("on_target"),
                       manifest.get("dwell_s"), manifest.get("rtb_on_complete"), assign])


def choose(results: list):
    """results: [{"drone": i, "ok": bool, "body": manifest}]. -> (выбранный манифест | None, борта с другим манифестом, борта без манифеста).
    Побеждает подпись, которую выдало больше бортов; при равенстве — та, что у борта с меньшим номером."""
    good = sorted((r for r in results if r.get("ok") and isinstance(r.get("body"), dict)), key=lambda r: r["drone"])
    if not good:
        return None, [], [r["drone"] for r in results]
    count = {}
    for r in good:
        count[signature(r["body"])] = count.get(signature(r["body"]), 0) + 1
    best = max(count.values())
    winner = next(r for r in good if count[signature(r["body"])] == best)       # первая по номеру среди лидеров
    sig = signature(winner["body"])
    dissent = [r["drone"] for r in good if signature(r["body"]) != sig]
    missing = [r["drone"] for r in results if r not in good]
    return winner["body"], dissent, missing
