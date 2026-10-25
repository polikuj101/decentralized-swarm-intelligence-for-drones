"""swarm/manifest_check.py — проверка Task Manifest при загрузке координатором.
manifest-N.json теперь лежит в томе и переживает пересоздание контейнера, поэтому файл с диска нельзя принимать вслепую:
повреждённый или подменённый манифест не должен молча менять состав активных секторов."""

ON_TARGET = ("DWELL_RECON", "DWELL_ONLY", "SKIP")
DWELL_MIN_S, DWELL_MAX_S = 3, 15


def manifest_problem(m, n_sectors: int):
    """None, если манифест допустим, иначе текст причины отказа."""
    if not isinstance(m, dict):
        return "манифест не объект"
    sectors = m.get("active_sectors")
    if not isinstance(sectors, list) or not sectors:
        return "active_sectors пуст или не список"
    if any(isinstance(s, bool) or not isinstance(s, int) or not 0 <= s < n_sectors for s in sectors):
        return f"active_sectors вне 0..{n_sectors - 1}: {sectors!r}"
    if m.get("on_target") not in ON_TARGET:
        return f"on_target не из {ON_TARGET}: {m.get('on_target')!r}"
    dwell = m.get("dwell_s")
    if isinstance(dwell, bool) or not isinstance(dwell, int) or not DWELL_MIN_S <= dwell <= DWELL_MAX_S:
        return f"dwell_s вне {DWELL_MIN_S}..{DWELL_MAX_S}: {dwell!r}"
    return None
