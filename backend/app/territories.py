"""
Деление города на зоны медпредов.

Используется взвешенная диаграмма Вороного (power diagram): у каждой зоны есть центр c и вес w,
точка x относится к зоне с минимальным |x − c|² − w. Границы между зонами — прямые,
поэтому зоны — выпуклые многоугольники, которые вместе покрывают весь город без пропусков.

Автоматический режим подбирает центры и веса так, чтобы в зонах было поровну врачей
(взвешенный k-means с выравниванием). В ручном режиме центры и веса задаёт пользователь,
перетаскивая зоны на карте; распределение учреждений следует за его границами.
"""
from __future__ import annotations

import math

import numpy as np


class Plane:
    """Плоские координаты в км относительно широты города."""

    def __init__(self, lat0: float):
        self.kx = math.cos(math.radians(lat0)) * 111.32
        self.ky = 110.57

    def xy(self, items) -> np.ndarray:
        if not len(items):
            return np.zeros((0, 2))
        return np.array([[x["lon"] * self.kx, x["lat"] * self.ky] for x in items])

    def to_xy(self, latlon) -> np.ndarray:
        return np.array([[lo * self.kx, la * self.ky] for la, lo in latlon], dtype=float)

    def to_latlon(self, pts) -> list[list[float]]:
        return [[round(float(y) / self.ky, 6), round(float(x) / self.kx, 6)] for x, y in pts]


def assign(pts: np.ndarray, centers: np.ndarray, weights: np.ndarray) -> np.ndarray:
    if not len(pts):
        return np.zeros(0, dtype=int)
    d2 = ((pts[:, None, :] - centers[None, :, :]) ** 2).sum(-1) - weights[None, :]
    return d2.argmin(1)


def balance(pts: np.ndarray, w: np.ndarray, k: int, seed: int = 7, tol: float = 0.03):
    """Центры и веса зон с равной суммой w в каждой зоне.

    Подбор может «залипнуть» на удалённом скоплении учреждений (одна зона почти пустая), поэтому
    при перекосе больше 10% пробуются другие стартовые положения и берётся самое ровное деление.
    """
    best, best_dev = None, np.inf
    target = w.sum() / k if k else 1
    for s in [seed] + [s for s in range(1, 13) if s != seed]:
        c, wt = _balance_once(pts, w, k, s, tol)
        load = np.bincount(assign(pts, c, wt), weights=w, minlength=k)
        dev = float(np.abs(load - target).max() / target) if target else 0.0
        if dev < best_dev:
            best, best_dev = (c, wt), dev
        if dev <= 0.10:
            break
    return best


def _balance_once(pts: np.ndarray, w: np.ndarray, k: int, seed: int, tol: float):
    rng = np.random.default_rng(seed)
    p = w / w.sum()
    cs = [pts[rng.choice(len(pts), p=p)]]
    for _ in range(k - 1):
        d2 = np.min(((pts[:, None, :] - np.array(cs)[None, :, :]) ** 2).sum(-1), axis=1) * w
        cs.append(pts[rng.choice(len(pts), p=d2 / d2.sum())] if d2.sum() > 0 else pts[rng.integers(len(pts))])
    centers = np.array(cs, dtype=float)
    weights = np.zeros(k)
    target = w.sum() / k
    spread = float(np.median(((pts - pts.mean(0)) ** 2).sum(1))) or 1.0
    for outer in range(30):
        step = spread / k * 0.5
        for _ in range(120):
            a = assign(pts, centers, weights)
            load = np.bincount(a, weights=w, minlength=k)
            dev = (load - target) / target
            if np.abs(dev).max() <= tol:
                break
            weights -= step * dev     # перегруженная зона «сжимается», недогруженная растёт
            step *= 0.96
        new = np.array([np.average(pts[a == c], axis=0, weights=w[a == c]) if (a == c).any() else centers[c]
                        for c in range(k)])
        moved = np.abs(new - centers).max()
        centers = new
        if moved < 0.05:
            break
    # финальное выравнивание при закреплённых центрах. Шаг адаптивный: растёт, пока перекос
    # уменьшается, и сжимается при ухудшении — так зона дотягивается и до удалённых скоплений.
    step = spread / k * 0.2
    prev, best = np.inf, (np.inf, weights.copy())
    for _ in range(1500):
        a = assign(pts, centers, weights)
        load = np.bincount(a, weights=w, minlength=k)
        dev = (load - target) / target
        m = float(np.abs(dev).max())
        if m < best[0]:
            best = (m, weights.copy())
        if m <= tol:
            break
        step = step * 1.05 if m < prev else step * 0.6
        prev = m
        weights -= step * dev
    weights = best[1] - best[1].mean()
    return centers, weights


def bounding_polygon(pts: np.ndarray, pad_km: float = 1.5) -> np.ndarray:
    """Выпуклая оболочка всех учреждений города с отступом — «рамка» для зон."""
    hull = convex_hull(pts)
    if len(hull) < 3:
        c = pts.mean(0) if len(pts) else np.zeros(2)
        return np.array([c + [-5, -5], c + [5, -5], c + [5, 5], c + [-5, 5]])
    c = hull.mean(0)
    out = []
    for p in hull:
        v = p - c
        n = np.linalg.norm(v) or 1
        out.append(p + v / n * pad_km)
    return np.array(out)


def convex_hull(pts: np.ndarray) -> np.ndarray:
    P = sorted(set(map(tuple, np.round(pts, 4))))
    if len(P) < 3:
        return np.array(P)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in P:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(P):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return np.array(lo[:-1] + up[:-1])


def _clip(poly: list, a: np.ndarray, b: float) -> list:
    """Отсечение многоугольника полуплоскостью a·x ≤ b (Сазерленд — Ходжман)."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp, fq = a @ p - b, a @ q - b
        if fp <= 0:
            out.append(p)
        if fp * fq < 0:
            t = fp / (fp - fq)
            out.append(p + t * (q - p))
    return out


def cells(centers: np.ndarray, weights: np.ndarray, frames: list[np.ndarray]) -> list[np.ndarray]:
    """Многоугольники зон: для зоны i пересечение полуплоскостей |x−ci|²−wi ≤ |x−cj|²−wj
    внутри её рамки (граница города или граница, расширенная за город)."""
    res = []
    for i in range(len(centers)):
        poly = [p.astype(float) for p in frames[i]]
        for j in range(len(centers)):
            if i == j or not poly:
                continue
            a = 2 * (centers[j] - centers[i])
            b = centers[j] @ centers[j] - centers[i] @ centers[i] - weights[j] + weights[i]
            poly = _clip(poly, a, b)
        res.append(np.array(poly))
    return res


def inside(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Точки внутри выпуклого многоугольника (вершины против часовой стрелки)."""
    if not len(pts) or len(poly) < 3:
        return np.zeros(len(pts), dtype=bool)
    ok = np.ones(len(pts), dtype=bool)
    for a, b in zip(poly, np.roll(poly, -1, axis=0)):
        ok &= (b[0] - a[0]) * (pts[:, 1] - a[1]) - (b[1] - a[1]) * (pts[:, 0] - a[0]) >= -1e-9
    return ok


def in_polygon(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Точки внутри произвольного (не обязательно выпуклого) многоугольника — метод луча."""
    res = np.zeros(len(pts), dtype=bool)
    if len(poly) < 3 or not len(pts):
        return res
    x, y = pts[:, 0], pts[:, 1]
    for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, axis=0)):
        cross = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        res ^= cross & (x < xi)
    return res


def _nearest_poly(polys: list[np.ndarray], pts: np.ndarray, ids: list[int]) -> np.ndarray:
    """Для точек вне всех областей — ближайшая область (по ближайшей вершине)."""
    best = np.full(len(pts), -1)
    dist = np.full(len(pts), np.inf)
    for poly, r in zip(polys, ids):
        if len(poly) < 1:
            continue
        d = ((pts[:, None, :] - poly[None, :, :]) ** 2).sum(-1).min(1)
        better = d < dist
        best[better], dist[better] = r, d[better]
    return best


def _apply_manual_shapes(manual: dict, plane: Plane, P, Q, reps, a_l, a_p, polys):
    """Ручные правки поверх деления.

    manual["polygons"] — границы всех зон, отредактированные вручную (по многоугольнику на МП):
    учреждение относится к зоне, в которую попадает, а вне всех зон — к ближайшей.
    manual["drawn"] — нарисованные области [{rep, polygon}]: всё внутри ДОБАВЛЯЕТСЯ к зоне этого МП,
    остальные границы не меняются. Позже нарисованная область перекрывает раньше нарисованную.
    """
    allxy = np.vstack([P, Q]) if len(Q) else P
    nP = len(P)
    a = np.concatenate([a_l, a_p]).astype(int)
    edited = manual.get("polygons")
    if edited and len(edited) == reps:
        polys = [plane.to_xy(x) if x and len(x) >= 3 else np.zeros((0, 2)) for x in edited]
        a = np.full(len(allxy), -1)
        for r, sh in enumerate(polys):
            a[in_polygon(sh, allxy)] = r
        out = a < 0
        if out.any():
            a[out] = _nearest_poly(polys, allxy[out], list(range(reps)))
    for d in manual.get("drawn") or []:
        if len(d.get("polygon") or []) >= 3:
            a[in_polygon(plane.to_xy(d["polygon"]), allxy)] = int(d["rep"])
    return a[:nP], a[nP:], polys


def _must_ids(shapes, plane: Plane, items: list[dict]) -> list[str]:
    if not shapes or not items:
        return []
    xy = plane.xy(items)
    mask = np.zeros(len(items), dtype=bool)
    for _, sh in shapes:
        mask |= in_polygon(sh, xy)
    return [f["id"] for f, m in zip(items, mask) if m]


def drawn_shapes(manual: dict | None, plane: Plane) -> list[tuple[int, np.ndarray]]:
    return [(int(d["rep"]), plane.to_xy(d["polygon"])) for d in (manual or {}).get("drawn") or []
            if len(d.get("polygon") or []) >= 3]


def partition(lpu: list[dict], pharm: list[dict], reps: int, center, manual: dict | None = None,
              sub_lpu: list[dict] | None = None, sub_pharm: list[dict] | None = None) -> dict:
    """Зоны для reps медпредов.

    manual = {"centers": [[lat, lon], ...], "weights": [...], "extensions": [км, ...]} — границы с карты.
    extensions — на сколько км зона выходит за границу города; учреждения пригородов
    (sub_lpu, sub_pharm) попадают в зону, только если она расширена до них.
    """
    plane = Plane(center[0])
    P = plane.xy(lpu)
    Q = plane.xy(pharm)
    ext = [float(x or 0) for x in ((manual or {}).get("extensions") or [0] * reps)][:reps]
    ext += [0.0] * (reps - len(ext))
    allp = np.vstack([P, Q]) if len(Q) else P
    # нагрузка зоны — врачи основной специальности: именно они задают, скольких врачей медпред
    # охватит за проект (дни × визиты в день). Доп. специальности ставятся в те же ЛПУ и дни с
    # постоянным числом визитов, поэтому на выравнивание зон не влияют — иначе при многочисленных
    # терапевтах зоны выходят равными по терапевтам и неравными (в 2–3 раза) по основной специальности.
    w = np.array([f.get("balance_weight", len(f["docs"])) for f in lpu], dtype=float)
    if lpu and not w.sum():   # нет врачей основной специальности — выравниваем по остальным
        w = np.array([sum(len(v) for v in f.get("docs_x", {}).values()) for f in lpu], dtype=float)

    if manual and len(manual.get("centers") or []) == reps:
        centers = plane.to_xy(manual["centers"])
        weights = np.array(manual.get("weights") or [0.0] * reps, dtype=float)
    elif reps == 1 or not len(allp):
        origin = np.average(P, axis=0, weights=w) if len(P) else allp.mean(0) if len(allp) else np.zeros(2)
        centers = origin[None, :]
        centers = np.repeat(centers, reps, axis=0)
        weights = np.zeros(reps)
    else:
        centers, weights = balance(P, w, reps) if len(P) else balance(Q, np.ones(len(Q)), reps)
        # нумерация по часовой стрелке от севера
        c0 = allp.mean(0)
        order = sorted(range(reps), key=lambda r: -math.atan2(centers[r][1] - c0[1], centers[r][0] - c0[0]))
        centers, weights = centers[order], weights[order]

    a_l = assign(P, centers, weights)
    a_p = assign(Q, centers, weights)
    frames = [bounding_polygon(allp, pad_km=max(1.5, e)) for e in ext] if len(allp) else []
    polys = cells(centers, weights, frames) if frames else [np.zeros((0, 2))] * reps
    if manual and (manual.get("polygons") or manual.get("drawn")) and len(allp):
        a_l, a_p, polys = _apply_manual_shapes(manual, plane, P, Q, reps, a_l, a_p, polys)
    # учреждения, переназначенные вручную кликом по точке
    moves = (manual or {}).get("assign") or {}
    if moves:
        a_l = np.array([int(moves.get(f["id"], c)) for f, c in zip(lpu, a_l)], dtype=int)
        a_p = np.array([int(moves.get(f["id"], c)) for f, c in zip(pharm, a_p)], dtype=int)
    spread = float(np.median(((allp - allp.mean(0)) ** 2).sum(1))) if len(allp) else 1.0
    out_l = [[f for f, c in zip(lpu, a_l) if c == r] for r in range(reps)]
    out_p = [[f for f, c in zip(pharm, a_p) if c == r] for r in range(reps)]
    sub_counts = [{"doctors": 0, "lpu": 0, "pharmacies": 0} for _ in range(reps)]
    # пригороды: только в зоны, расширенные за город, и только внутри их новой границы
    shapes = drawn_shapes(manual, plane)
    for items, out, key in ((sub_lpu or [], out_l, "lpu"), (sub_pharm or [], out_p, "pharmacies")):
        if not items or not frames or not (any(ext) or shapes):
            continue
        S = plane.xy(items)
        a_s = assign(S, centers, weights)
        own = np.full(len(S), -1)
        for r, sh in shapes:  # учреждения за городом внутри нарисованной области — этому МП
            own[in_polygon(sh, S)] = r
        taken = own >= 0
        for r in range(reps):
            mask = own == r
            if ext[r] > 0:
                mask |= ~taken & (a_s == r) & in_polygon(polys[r], S)
            for f in (x for x, m in zip(items, mask) if m):
                out[r].append(f)
                sub_counts[r][key] += 1
                if key == "lpu":
                    sub_counts[r]["doctors"] += len(f["docs"])
    return {
        "lpu": out_l,
        "pharm": out_p,
        "extensions": ext,
        "suburb_counts": sub_counts,
        "polygons": [plane.to_latlon(p) for p in polys],
        "centers": plane.to_latlon(centers),
        "weights": [round(float(x), 3) for x in weights],
        "weight_step": round(spread / max(reps, 1) * 0.3, 3),
        "manual": bool(manual),
        # учреждения внутри нарисованных областей — их врачи ставятся в план в первую очередь
        "must": _must_ids(shapes, plane, lpu + pharm + (sub_lpu or []) + (sub_pharm or [])),
    }
