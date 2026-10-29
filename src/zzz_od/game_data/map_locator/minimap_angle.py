"""小地图镜头朝向识别。

流程：
1. 按 HSV 范围过滤切出亮区，再做腐蚀、膨胀把圆心菱形和外面扇形断开。
   参数对应流水线 assets/image_analysis_pipelines/小地图方向识别.yml。
2. 镜头朝向：圆心用裁剪块的几何中心（角色标记恒定落在那里，实测偏差在
   1.5px 内），在固定半径的环里取亮区质心，质心方向即扇形方向。
   不再需要先拟合菱形、再用菱形半径算环带。
3. 角色朝向：保留两套算法，都算出来返回，供后续对比，当前不参与决策。
   - 顶点法：取距圆心最近的连通域，approxPolyDP 拟合四顶点（多则合并），
     两条对角线交点作中心，最远顶点方向即朝向。
   - 边拟合法：把轮廓点按四边形四边分段，各用 fitLine 拟合直线，
     相邻直线外延求交点作顶点。尖角被削平或多出 1px 粘连时，
     直线外延交点仍落在真实几何尖顶，精度比顶点法高。

角度约定：正右为 0，逆时针增加，取值 0~360。

本模块是插件 plugins/map_overlay 朝向算法的独立移植，两边不共享代码。
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from cv2.typing import MatLike

from one_dragon.utils import cv2_utils

BRIGHT_HSV_COLOR: tuple[int, int, int] = (24, 255, 255)
"""亮区中心颜色（OpenCV H 0~179、S 0~255、V 0~255）。"""

BRIGHT_HSV_DIFF: tuple[int, int, int] = (10, 200, 160)
"""亮区三通道容差。V 下限 95 把半透明扇形切在界外，菱形与扇形自动断开。"""

ERODE_KERNEL_SIZE: int = 1
"""腐蚀核大小。V 容差收到 160 后菱形与扇形已是独立连通域，1×1 等效于不处理。"""

ERODE_ITERATIONS: int = 1
"""腐蚀迭代次数。"""

DILATE_KERNEL_SIZE: int = 1
"""膨胀核大小。用 3×3 会把菱形尖角旁的亮区并进轮廓，多出顶点导致拟合失败。"""

DILATE_ITERATIONS: int = 1
"""膨胀迭代次数。"""

SECTOR_INNER_RADIUS: int = 18
SECTOR_OUTER_RADIUS: int = 40
"""求扇形方向的环形带，单位像素，以角色标记质心为圆心。

内边界 18 要大于角色菱形半径（实测 15.8），避开标记自身；外侧到 40 覆盖
扇形主体，实测贝果局内扇形亮区落在半径 18~40 之间。
"""

SECTOR_STEP_DEGREE: float = 0.5
"""沿角度扫描的分辨率。外圈 40 像素半径上 0.5 度约 0.35 像素，够细。"""

SECTOR_LIT_RATIO: float = 0.5
"""某个角度上环带里亮像素占比超过它，才算落在这个角度的扇形里。"""

SECTOR_MIN_SPAN: float = 10.0
SECTOR_MAX_SPAN: float = 180.0
"""可信的扇形张角范围。窄于下限只剩一个角，宽于上限说明半圈都亮着，都不可信。"""

VERTEX_EPSILON: int = 3
"""菱形顶点拟合精度。小了会把圆角切出多余顶点，大了会把真实的窄角抹平。"""

EDGE_FIT_MAX_DISTANCE: float = 2.5
"""轮廓点归入某条边的最大距离，超出即视为尖角附近的过渡点。"""

EDGE_FIT_MIN_POINTS: int = 4
"""一条边至少要这么多点才拟合；少了说明该边几乎被切光。"""

MIN_PIXELS: int = 30
"""少于这个像素数说明不是有效区域。"""


def _bright_mask(rgb: MatLike) -> np.ndarray:
    """按 HSV 范围过滤切出亮区，再腐蚀、膨胀断开菱形与扇形。

    Args:
        rgb: 小地图 RGB 图。

    Returns:
        取值 0/255 的亮区掩码。
    """
    mask = cv2_utils.filter_by_color(
        rgb, mode='hsv', hsv_color=BRIGHT_HSV_COLOR, hsv_diff=BRIGHT_HSV_DIFF
    )
    erode_kernel = np.ones((ERODE_KERNEL_SIZE, ERODE_KERNEL_SIZE), np.uint8)
    mask = cv2.erode(mask, erode_kernel, iterations=ERODE_ITERATIONS)
    dilate_kernel = np.ones((DILATE_KERNEL_SIZE, DILATE_KERNEL_SIZE), np.uint8)
    return cv2.dilate(mask, dilate_kernel, iterations=DILATE_ITERATIONS)


def _longest_lit_run(lit: np.ndarray) -> tuple[int, int]:
    """环形数组里最长的一段连续真值。

    Args:
        lit: 一整圈的角度命中标记。

    Returns:
        (起点下标, 长度)。全真时返回 (0, 长度)。
    """
    total = len(lit)
    if lit.all():
        return 0, total
    dark = np.where(~lit)[0]
    # 每个暗角到下一个暗角的间距，就是这个暗角之后那段亮区的长度加一
    gaps = np.diff(np.append(dark, dark[0] + total))
    index = int(np.argmax(gaps))
    return int(dark[index] + 1) % total, int(gaps[index]) - 1


def _sector_angle(
    mask: np.ndarray, center_x: float, center_y: float,
) -> float | None:
    """逐圈扫环带，找扇形两侧由暗转亮的两条边，取角平分线作为镜头朝向。

    扇形是个锥形：贴着角色的地方窄，往远端张开。所以不能把整条径向压成
    一个角度判定，否则结果被最窄的内圈带偏；这里每层半径各扫一圈，各自
    求两条边的中点，最后把各层中点按方向矢量平均。

    不用质心：扇形被墙切掉一块时，缺失会把质心拽向缺口，角度随之偏掉。

    Args:
        mask: 亮区掩码，角色标记自身的像素应当已经清掉。
        center_x: 圆心 x，取角色标记质心。
        center_y: 圆心 y。

    Returns:
        镜头朝向角度；扫不到可信扇形时返回 None。
    """
    height, width = mask.shape[:2]
    angles = np.deg2rad(np.arange(0.0, 360.0, SECTOR_STEP_DEGREE))
    cosines, sines = np.cos(angles), np.sin(angles)
    total = len(angles)
    sum_x = 0.0
    sum_y = 0.0
    layers = 0

    for radius in range(SECTOR_INNER_RADIUS, SECTOR_OUTER_RADIUS):
        xs = np.clip(np.round(center_x + radius * cosines).astype(np.int32), 0, width - 1)
        ys = np.clip(np.round(center_y - radius * sines).astype(np.int32), 0, height - 1)
        lit = mask[ys, xs] > 0
        if not lit.any():
            continue
        start, length = _longest_lit_run(lit)
        span = length * SECTOR_STEP_DEGREE
        if not (SECTOR_MIN_SPAN <= span <= SECTOR_MAX_SPAN):
            continue
        middle = math.radians(((start + length / 2) % total) * SECTOR_STEP_DEGREE)
        sum_x += math.cos(middle)
        sum_y += math.sin(middle)
        layers += 1

    if layers == 0 or (sum_x == 0 and sum_y == 0):
        return None
    return math.degrees(math.atan2(sum_y, sum_x)) % 360


def _marker_component(
    mask: np.ndarray, center_x: float, center_y: float,
) -> np.ndarray | None:
    """取距圆心最近的那个连通域，就是角色菱形。

    Args:
        mask: 亮区掩码。
        center_x: 圆心 x。
        center_y: 圆心 y。

    Returns:
        0/1 掩码；找不到时返回 None。
    """
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)

    label = 0
    best = float('inf')
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < MIN_PIXELS:
            continue
        distance = math.hypot(
            centroids[index][0] - center_x, centroids[index][1] - center_y
        )
        if distance < best:
            best = distance
            label = index
    if label == 0:
        return None
    return (labels == label).astype(np.uint8)


def _merge_to_four(polygon: np.ndarray) -> np.ndarray:
    """把最相邻的两个顶点合并成中点，直到只剩四个顶点。

    HSV 掩码边界不干净时（尖角被亮区并进来），approxPolyDP 会多切出顶点，
    典型是尖角变成一小段平边、同一侧出现两个点。逐个吞并最近的一对，
    最后总能回到四个顶点，避免整帧判失败。

    Args:
        polygon: approxPolyDP 拟合出的顶点，至少四个。

    Returns:
        四个顶点。原本就是四个时原样返回。
    """
    points = [point.astype(np.float64) for point in polygon]
    while len(points) > 4:
        count = len(points)
        near_index = 0
        near_distance = float('inf')
        for index in range(count):
            next_index = (index + 1) % count
            gap = float(np.hypot(*(points[index] - points[next_index])))
            if gap < near_distance:
                near_distance = gap
                near_index = index
        next_index = (near_index + 1) % count
        middle = (points[near_index] + points[next_index]) / 2
        points = [
            points[index] for index in range(count)
            if index not in (near_index, next_index)
        ]
        points.insert(min(near_index, len(points)), middle)
    return np.array(points)


def _cross_point(
    first_a: np.ndarray, first_b: np.ndarray, second_a: np.ndarray, second_b: np.ndarray
) -> np.ndarray | None:
    """两条直线的交点。

    Args:
        first_a: 第一条线端点。
        first_b: 第一条线另一端点。
        second_a: 第二条线端点。
        second_b: 第二条线另一端点。

    Returns:
        交点坐标，两线平行时返回 None。
    """
    x1, y1 = first_a
    x2, y2 = first_b
    x3, y3 = second_a
    x4, y4 = second_b
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-9:
        return None
    first = x1 * y2 - y1 * x2
    second = x3 * y4 - y3 * x4
    return np.array([
        (first * (x3 - x4) - (x1 - x2) * second) / denominator,
        (first * (y3 - y4) - (y1 - y2) * second) / denominator,
    ])


def _tip_from_vertices(
    vertices: np.ndarray,
) -> tuple[float, float, float, np.ndarray] | None:
    """从四个顶点算菱形朝向。

    Args:
        vertices: 四个顶点坐标，顺序沿轮廓。

    Returns:
        (朝向角, 半径 A, 中心 x, 中心 y)，退化时返回 None。
    """
    origin = _cross_point(vertices[0], vertices[2], vertices[1], vertices[3])
    if origin is None:
        return None
    distances = np.hypot(vertices[:, 0] - origin[0], vertices[:, 1] - origin[1])
    tip = vertices[int(np.argmax(distances))]
    offset_x = tip[0] - origin[0]
    offset_y = tip[1] - origin[1]
    if offset_x == 0 and offset_y == 0:
        return None
    angle = math.degrees(math.atan2(-offset_y, offset_x)) % 360
    return angle, float(distances.max()), float(origin[0]), float(origin[1])


def _marker_by_vertices(
    component: np.ndarray,
) -> tuple[float, float, float, float] | None:
    """顶点法：approxPolyDP 拟合四顶点，对角线交点作中心。

    Args:
        component: 角色菱形的 0/1 掩码。

    Returns:
        (朝向角, 半径 A, 中心 x, 中心 y)，拟合不出四边形时返回 None。
    """
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    polygon = cv2.approxPolyDP(contour, VERTEX_EPSILON, True).reshape(-1, 2)
    if len(polygon) < 4:
        return None
    result = _tip_from_vertices(_merge_to_four(polygon))
    if result is None:
        return None
    angle, radius, origin_x, origin_y = result
    return angle, radius, origin_x, origin_y


def _edge_groups(
    contour: np.ndarray, polygon: np.ndarray,
) -> list[np.ndarray]:
    """把轮廓点按四边形四条边分段，返回每边的点集。

    Args:
        contour: 完整轮廓点（CHAIN_APPROX_NONE）。
        polygon: 用作分段基准的四个顶点。

    Returns:
        四组点，顺序与 polygon 的边一致。
    """
    points = contour.reshape(-1, 2).astype(np.float64)
    groups: list[list[np.ndarray]] = [[] for _ in range(4)]
    for point in points:
        near_index = 0
        near_distance = float('inf')
        for index in range(4):
            start = polygon[index]
            end = polygon[(index + 1) % 4]
            edge = end - start
            length_squared = float(edge @ edge)
            if length_squared < 1e-9:
                continue
            ratio = np.clip(float((point - start) @ edge) / length_squared, 0.0, 1.0)
            distance = float(np.hypot(*(point - (start + ratio * edge))))
            if distance < near_distance:
                near_distance = distance
                near_index = index
        if near_distance < EDGE_FIT_MAX_DISTANCE:
            groups[near_index].append(point)
    return [np.array(group, dtype=np.float64) for group in groups]


def _fit_line(points: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """用最小二乘拟合一条直线，并剔除偏离过远的点后重拟一次。

    Args:
        points: 该边上的轮廓点。

    Returns:
        (线上一点, 单位方向向量)；点太少时返回 None。
    """
    if len(points) < EDGE_FIT_MIN_POINTS:
        return None
    samples = points.astype(np.float32)
    vx, vy, x0, y0 = cv2.fitLine(samples, cv2.DIST_L2, 0, 0.01, 0.01).ravel()
    direction = np.array([float(vx), float(vy)])
    norm = float(np.linalg.norm(direction))
    if norm < 1e-9:
        return None
    direction /= norm
    normal = np.array([-direction[1], direction[0]])
    origin = np.array([float(x0), float(y0)])
    offsets = np.abs((points - origin) @ normal)
    keep = offsets < max(0.5, float(np.median(offsets)) * 2.0)
    refined = points[keep]
    if len(refined) >= EDGE_FIT_MIN_POINTS:
        vx, vy, x0, y0 = cv2.fitLine(
            refined.astype(np.float32), cv2.DIST_L2, 0, 0.01, 0.01,
        ).ravel()
        direction = np.array([float(vx), float(vy)])
        norm = float(np.linalg.norm(direction))
        if norm < 1e-9:
            return None
        direction /= norm
        origin = np.array([float(x0), float(y0)])
    return origin, direction


def _marker_by_edges(component: np.ndarray) -> tuple[float, float, float, float] | None:
    """边拟合法：四条边分别拟合直线，相邻直线外延交点作顶点。

    顶点法的顶点受像素栅格限制，只能落在整数坐标上；尖角被削平还会整个失败。
    直线外延的交点是连续解，能落在真实几何尖顶上，精度是亚像素级的。

    Args:
        component: 角色菱形的 0/1 掩码。

    Returns:
        (朝向角, 半径 A, 中心 x, 中心 y)，任一步失败时返回 None。
    """
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(contour, VERTEX_EPSILON, True).reshape(-1, 2)
    if len(approx) < 4:
        return None
    polygon = _merge_to_four(approx)

    lines = []
    for group in _edge_groups(contour, polygon):
        line = _fit_line(group)
        if line is None:
            return None
        lines.append(line)

    vertices = []
    for index in range(4):
        (origin_a, direction_a) = lines[index]
        (origin_b, direction_b) = lines[(index + 1) % 4]
        cross = _cross_point(
            origin_a, origin_a + direction_a, origin_b, origin_b + direction_b,
        )
        if cross is None:
            return None
        vertices.append(cross)
    result = _tip_from_vertices(np.array(vertices))
    if result is None:
        return None
    angle, radius, origin_x, origin_y = result
    return angle, radius, origin_x, origin_y


def analyze(
    rgb: MatLike,
) -> tuple[float | None, float | None, float | None, float | None, float, tuple[float, float]]:
    """一次算出两套角色朝向、镜头朝向与圆心。

    Args:
        rgb: 小地图 RGB 图，正方形。

    Returns:
        (角色朝向·顶点法, 角色朝向·边拟合法, 镜头朝向, 菱形半径, 圆心)。
        算不出的项为 None，半径算不出时为 0。圆心恒为裁剪块几何中心。
    """
    height, width = rgb.shape[:2]
    center_x, center_y = (width - 1) / 2, (height - 1) / 2
    center = (center_x, center_y)

    mask = _bright_mask(rgb)
    if int(mask.sum()) < MIN_PIXELS:
        return None, None, None, 0.0, center

    component = _marker_component(mask, center_x, center_y)
    if component is None:
        sector_value = _sector_angle(mask, center_x, center_y)
        return None, None, sector_value, 0.0, center

    # 角色标记并不总在裁剪块正中：贝果局内实测偏出 30 像素上下，
    # 拿几何中心当圆心会把环带整个扫到空地上去，所以先找标记再定圆心。
    marker_ys, marker_xs = np.nonzero(component)
    sector_mask = mask.copy()
    sector_mask[marker_ys, marker_xs] = 0
    sector_value = _sector_angle(
        sector_mask, float(marker_xs.mean()), float(marker_ys.mean()),
    )

    vertices_result = _marker_by_vertices(component)
    edges_result = _marker_by_edges(component)
    marker_vertices = vertices_result[0] if vertices_result is not None else None
    marker_edges = edges_result[0] if edges_result is not None else None
    radius = vertices_result[1] if vertices_result is not None else 0.0
    return marker_vertices, marker_edges, sector_value, radius, center
