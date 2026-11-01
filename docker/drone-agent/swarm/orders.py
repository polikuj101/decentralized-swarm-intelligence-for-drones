"""swarm/orders.py — параметры приказа оператора, которые реально исполняет рой (чистые функции, без ROS)."""

DEFAULT_DWELL_S = 5.0     # как в mission.json: поведение до появления приказа


def order_params(manifest, default_dwell_s=DEFAULT_DWELL_S, drone_id=None) -> dict:
    """Манифест -> действующие параметры. Нет манифеста (запасной путь по тайм-ауту): прежнее поведение.
    on_target: SKIP — цели не обслуживаются; DWELL_RECON — зависнуть и снять кадр; DWELL_ONLY — зависнуть над целью без снимка.
    allowed: сектора, которыми этому борту разрешено заниматься (приказ с адресацией); None — любые активные."""
    m = manifest or {}
    on_target = m.get("on_target", "DWELL_RECON")
    dwell = m.get("dwell_s")
    mine = (m.get("assignments") or {}).get(str(drone_id)) if drone_id is not None else None
    return {
        "allowed": sorted(mine) if mine else None,
        "skip_targets": on_target == "SKIP",
        "capture": on_target == "DWELL_RECON",
        "dwell_s": float(dwell) if isinstance(dwell, (int, float)) and not isinstance(dwell, bool) else float(default_dwell_s),
        "rtb": bool(m.get("rtb_on_complete", True)),
        "on_target": on_target,
    }


def sector_allowed(params: dict, zone: int) -> bool:
    """Может ли борт торговаться за задачу из этого сектора: без адресации (allowed=None) — да, с адресацией — только за свои."""
    allowed = params.get("allowed")
    return allowed is None or zone in allowed


def task_enabled(kind: str, params: dict) -> bool:
    """SKIP убирает из списка открытых задач все цели; сектора остаются."""
    return not (kind == "TARGET" and params["skip_targets"])


def target_dwell_s(kind: str, params: dict) -> float:
    return params["dwell_s"] if kind == "TARGET" else 0.0


def manifest_signature(m) -> tuple:
    """Содержимое приказа, влияющее на поведение. manifest_id для новизны недостаточен: две компиляции в одну секунду
    получают один id, а выровненный манифест несёт id большинства."""
    m = m or {}
    assign = tuple(sorted((k, tuple(sorted(v))) for k, v in (m.get("assignments") or {}).items()))
    return (tuple(sorted(m.get("active_sectors") or [])), m.get("on_target"), m.get("dwell_s"), m.get("rtb_on_complete"), assign)


def needs_rebuild(old: dict, new: dict) -> bool:
    """Нужно ли пересобрать аукцион: изменился набор открытых задач (SKIP) или список секторов этого борта."""
    return old["skip_targets"] != new["skip_targets"] or old.get("allowed") != new.get("allowed")
