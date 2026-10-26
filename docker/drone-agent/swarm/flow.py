"""swarm/flow.py — устойчивый сдвиг кадра нижней камеры (чистые функции, numpy + OpenCV, без ROS).

Прогон без GPS 2026-10-26 (docs/gz_leak/vio_eval.py): D5 висел неподвижно, под ним на 2,3 м ниже прошёл D2. Фазовая корреляция по всему
кадру ухватилась за движущийся борт (сдвиги 5-36 пикселей при отклике 0,6-0,8 — по отклику не отличить), часть ложных сдвигов прошла
отбраковку, EKF поверил, и регулятор увёл борт на 8,6 м. Здесь сдвиг считается в сетке плиток и берётся медиана: посторонний объект
занимает лишь часть плиток. Если с медианой согласна малая доля плиток — в кадре нет единого движения фона, кадр не годится."""
import cv2
import numpy as np

GRID = 3
AGREE_PX = 1.5           # плитка согласна с медианой, если её сдвиг отличается не больше чем на столько пикселей
AGREE_MIN = 0.6          # доля согласных плиток, ниже — кадр отбрасывается
TILE_RESP_MIN = 0.05
# Плитка без текстуры (крыша препятствия, диск-цель, небо за краем пола) не голосует: однородная картинка даёт «уверенный» нулевой сдвиг
# (прогон 2026-10-26: D5 вышел за край пола, VIO при отклике 0,93 и согласии всех плиток сообщал «стою», борт разогнался и улетел на 460 м).
# Разброс яркости плиток пола 0,017-0,04 (кадры над целями, препятствиями и полом), однородных участков — около 0.
TEX_MIN = 0.01

_windows = {}


def _win(w, h):
    k = (w, h)
    if k not in _windows:
        _windows[k] = cv2.createHanningWindow((w, h), cv2.CV_32F)
    return _windows[k]


def robust_shift(prev: np.ndarray, img: np.ndarray, grid: int = GRID):
    """-> (dx, dy, отклик, доля согласных плиток) или None, если голосовать нечему. prev, img — float32 одного размера."""
    h, w = img.shape
    th, tw = h // grid, w // grid
    win = _win(tw, th)
    votes = []
    for i in range(grid):
        for j in range(grid):
            a = prev[i * th:(i + 1) * th, j * tw:(j + 1) * tw]
            b = img[i * th:(i + 1) * th, j * tw:(j + 1) * tw]
            if a.std() < TEX_MIN or b.std() < TEX_MIN:
                continue
            (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
            if resp >= TILE_RESP_MIN:
                votes.append((dx, dy, resp))
    if len(votes) < 3:
        return None
    v = np.array(votes)
    mx, my = float(np.median(v[:, 0])), float(np.median(v[:, 1]))
    agree = np.hypot(v[:, 0] - mx, v[:, 1] - my) <= AGREE_PX
    frac = float(agree.sum()) / (grid * grid)
    if not agree.any():
        return mx, my, 0.0, 0.0
    sel = v[agree]
    return float(sel[:, 0].mean()), float(sel[:, 1].mean()), float(np.median(sel[:, 2])), frac


def validated_shift(prev: np.ndarray, img: np.ndarray):
    """-> (dx, dy, отклик, доля согласных плиток, причина отказа | None). Сдвиг меряется по всему кадру (по плиткам с окном Ханна на гладкой
    текстуре пола он занижен: масштаб VIO 0,82-0,91 против 0,96-1,06 по всему кадру, прогоны 2026-10-26), а плитки его проверяют:
    если сдвиг всего кадра расходится с согласным большинством плиток, его тянет посторонний объект — кадр не годится."""
    tiles = robust_shift(prev, img)
    if tiles is None:
        return 0.0, 0.0, 0.0, 0.0, "нет текстуры"
    tx, ty, _, agree = tiles
    if agree < AGREE_MIN:
        return tx, ty, 0.0, agree, "нет единого движения фона"
    h, w = img.shape
    (dx, dy), resp = cv2.phaseCorrelate(prev, img, _win(w, h))
    if np.hypot(dx - tx, dy - ty) > max(AGREE_PX, 0.25 * np.hypot(tx, ty)):
        return dx, dy, resp, agree, "сдвиг кадра расходится с плитками"
    return dx, dy, resp, agree, None
