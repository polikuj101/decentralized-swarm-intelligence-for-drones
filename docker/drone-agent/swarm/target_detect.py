"""swarm/target_detect.py — обнаружение мишени-диска на кадре нижней камеры (чистые функции, numpy + OpenCV, без ROS).

Мишени мира — плоские диски радиусом 1,5 м. Камера отдаёт L8, то есть фактически красный канал (кадры 2026-10-26 над целями):
красный диск получается почти белым (~226), зелёный и синий — чёрными (0), пол — серая текстура 35-165 с плавными перепадами
(в окне 7×7 пол так же «ровный», как диск, поэтому однородность сама по себе не признак), крыши препятствий — ровный серый
прямоугольник с резкими прямыми краями. Признаки диска: резкая граница, которая на всей видимой дуге идёт по окружности; радиус
согласуется с высотой по дальномеру; внутри однородно; яркость резко отличается от кольца вокруг. Цвет по такому кадру не определить,
поэтому детектор сообщает «светлый»/«тёмный» диск.
Решение владельца продукта 2026-10-26 (HANDOFF п. 4): карточка BDA ссылается на обнаружение в кадре, а не на ближайшую цель из сцены."""
import math

import cv2
import numpy as np

TARGET_RADIUS_M = 1.5
RADIUS_TOL = 0.35            # допуск радиуса относительно ожидаемого по высоте
EDGE_GRAD = 25.0             # перепад яркости на границе (Собель, на пиксель); у текстуры пола он плавный
EDGE_SUPPORT = 0.7           # доля видимой окружности, где есть резкая граница (у прямоугольника — только у углов вписанной дуги)
MIN_ARC_DEG = 120.0          # видимая часть окружности на кадре
INSIDE_TOL, INSIDE_FRAC = 12.0, 0.8   # однородность: доля пикселей внутри, близких к медиане (опора шасси может закрыть край диска)
MIN_CONTRAST = 30.0          # разница медианной яркости диска и кольца вокруг
N_SAMPLES = 72


def _inside_frame(x, y, w, h, m=2):
    return (x >= m) & (x < w - m) & (y >= m) & (y < h - m)


def _check(g, grad, cx, cy, r, w, h):
    t = np.linspace(0.0, 2.0 * np.pi, N_SAMPLES, endpoint=False)
    px, py = cx + r * np.cos(t), cy + r * np.sin(t)
    vis = _inside_frame(px, py, w, h)
    arc_deg = 360.0 * vis.mean()
    if arc_deg < MIN_ARC_DEG:
        return None
    # Хаф часто ошибается в радиусе и центре на 1-2 пикселя: граница ищется вдоль радиуса в пределах ±tol
    tol = max(2, int(round(0.05 * r)))
    tv = t[vis]
    edge = np.zeros(len(tv), bool)
    for dr in range(-tol, tol + 1):
        gx = np.clip(np.round(cx + (r + dr) * np.cos(tv)).astype(int), 0, w - 1)
        gy = np.clip(np.round(cy + (r + dr) * np.sin(tv)).astype(int), 0, h - 1)
        edge |= grad[gy, gx] > EDGE_GRAD
    support = float(edge.mean())
    if support < EDGE_SUPPORT:
        return None
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - cx, yy - cy)
    inside, ring = g[d < 0.8 * r], g[(d > 1.2 * r) & (d < 1.5 * r)]
    if inside.size < 50 or ring.size < 50:
        return None
    med = float(np.median(inside))
    contrast = med - float(np.median(ring))
    if abs(contrast) < MIN_CONTRAST or float((np.abs(inside - med) < INSIDE_TOL).mean()) < INSIDE_FRAC:
        return None
    return {"cx": round(float(cx), 1), "cy": round(float(cy), 1), "r_px": round(float(r), 1), "arc_deg": round(arc_deg),
            "edge_support": round(support, 2), "brightness": round(med), "contrast": round(contrast),
            "tone": "светлый" if contrast > 0 else "тёмный"}


def detect_disc(img: np.ndarray, height_m=None, fx=None):
    """-> словарь обнаружения или None. height_m — высота камеры над землёй (дальномер), fx — фокус в пикселях;
    без них радиус ищется во всём диапазоне кадра."""
    h, w = img.shape[:2]
    g = img.astype(np.float32)
    grad = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)) / 4.0
    r_exp = TARGET_RADIUS_M * fx / height_m if height_m and fx and height_m > 0.3 else None
    rmin, rmax = (max(5, int(r_exp * (1 - RADIUS_TOL))), int(r_exp * (1 + RADIUS_TOL)) + 1) if r_exp else (8, max(h, w))
    blur = cv2.GaussianBlur(img, (5, 5), 1.2)
    circles = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.5, minDist=10, param1=100, param2=20, minRadius=rmin, maxRadius=rmax)
    best = None
    for cx, cy, r in ([] if circles is None else circles[0][:20]):
        cand = _check(g, grad, cx, cy, r, w, h)
        if cand and (best is None or cand["edge_support"] * cand["arc_deg"] > best["edge_support"] * best["arc_deg"]):
            best = cand
    if best is not None:
        best["r_expected_px"] = None if r_exp is None else round(r_exp, 1)
    return best


def pixel_to_world(cx_px, cy_px, w, h, height_m, fx, heading_rad, x_e, y_n):
    """Центр диска на кадре -> ENU (x=восток, y=север), м. Камера смотрит вниз, верх кадра — нос борта, право — правый борт
    (та же раскладка, что в vio_node); heading — курс PX4 (NED, 0 = север)."""
    s = height_m / fx
    fwd, right = -(cy_px - h / 2.0) * s, (cx_px - w / 2.0) * s
    c, sn = math.cos(heading_rad), math.sin(heading_rad)
    d_n, d_e = fwd * c - right * sn, fwd * sn + right * c
    return x_e + d_e, y_n + d_n
