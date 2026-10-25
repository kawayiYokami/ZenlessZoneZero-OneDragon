"""小地图角色朝向与镜头朝向识别。

流程：
1. 按色相切出亮区，腐蚀 1px 把圆心菱形和外面扇形断开。
2. 取距圆心最近的连通域，就是玩家菱形。拟合出它的四个顶点，
   两条对角线交点作为菱形中心，到交点最远的顶点方向即菱形方向，
   该距离记为半径 A。
3. 以交点为原点取 A+3 ~ A+8 的环，环内亮区质心方向即扇形方向。

角度约定：正右为 0，逆时针增加，取值 0~360。

本模块是插件 plugins/map_overlay 朝向算法的独立移植，两边不共享代码。
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from cv2.typing import MatLike

BRIGHT_HUE_CENTER = 24
"""亮区色相中心（OpenCV H 通道 0~180）。S/V 不设限，只靠色相区分。"""

BRIGHT_HUE_TOLERANCE = 10
"""亮区色相允许的上下浮动。"""

ERODE_KERNEL = np.ones((3, 3), np.uint8)
"""腐蚀核，把菱形和扇形之间的细连接断开。"""

VERTEX_EPSILON = 3
"""菱形顶点拟合精度。小了会把圆角切出多余顶点，大了会把真实的窄角抹平。"""

RING_INNER_OFFSET = 3
"""环形带内边界相对菱形顶点半径 A 的偏移。"""

RING_OUTER_OFFSET = 8
"""环形带外边界相对菱形顶点半径 A 的偏移。"""

MIN_PIXELS = 30
"""少于这个像素数说明不是有效区域。"""


def _bright_mask(rgb: MatLike) -> np.ndarray:
    """按色相切出亮区。

    Args:
        rgb: 小地图 RGB 图。

    Returns:
        取值 0/1 的亮区掩码。
    """
    hue = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 0]
    low = BRIGHT_HUE_CENTER - BRIGHT_HUE_TOLERANCE
    high = BRIGHT_HUE_CENTER + BRIGHT_HUE_TOLERANCE
    return ((hue >= low) & (hue <= high)).astype(np.uint8)


def _find_marker(mask: np.ndarray, center_x: float, center_y: float) -> np.ndarray | None:
    """腐蚀断开后取距圆心最近的连通域，返回它的四个顶点。

    Args:
        mask: 亮区掩码。
        center_x: 小地图圆心 x。
        center_y: 小地图圆心 y。

    Returns:
        四个顶点坐标，拟合不出四边形时返回 None。
    """
    eroded = cv2.erode(mask, ERODE_KERNEL, iterations=1)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(eroded, 8)

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

    component = (labels == label).astype(np.uint8)
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    polygon = cv2.approxPolyDP(contour, VERTEX_EPSILON, True).reshape(-1, 2)
    if len(polygon) != 4:
        return None
    return polygon.astype(np.float64)


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


def analyze(rgb: MatLike) -> tuple[float | None, float | None, float, tuple[float, float] | None]:
    """一次算出菱形方向、扇形方向、菱形顶点半径与菱形中心。

    Args:
        rgb: 小地图 RGB 图，正方形。

    Returns:
        (角色朝向, 镜头朝向, 半径 A, 菱形中心)。算不出的项为 None，半径算不出时为 0。
    """
    height, width = rgb.shape[:2]
    center_x, center_y = (width - 1) / 2, (height - 1) / 2

    mask = _bright_mask(rgb)
    if int(mask.sum()) < MIN_PIXELS:
        return None, None, 0.0, None

    polygon = _find_marker(mask, center_x, center_y)
    if polygon is None:
        return None, None, 0.0, None

    # 四个顶点两两对边连线的交点，作为菱形中心
    origin = _cross_point(polygon[0], polygon[2], polygon[1], polygon[3])
    if origin is None:
        return None, None, 0.0, None

    distances = np.hypot(polygon[:, 0] - origin[0], polygon[:, 1] - origin[1])
    tip = polygon[int(np.argmax(distances))]
    radius_a = float(distances.max())

    offset_x = tip[0] - origin[0]
    offset_y = tip[1] - origin[1]
    if offset_x == 0 and offset_y == 0:
        return None, None, 0.0, (float(origin[0]), float(origin[1]))
    marker_value = math.degrees(math.atan2(-offset_y, offset_x)) % 360

    # 以菱形中心为原点，取 A 之外的环
    inner = radius_a + RING_INNER_OFFSET
    outer = radius_a + RING_OUTER_OFFSET
    ys, xs = np.nonzero(mask)
    radii = np.hypot(xs - origin[0], ys - origin[1])
    keep = (radii >= inner) & (radii < outer)
    origin_tuple = (float(origin[0]), float(origin[1]))
    if int(keep.sum()) < MIN_PIXELS:
        return marker_value, None, radius_a, origin_tuple

    sector_x = float(xs[keep].mean()) - origin[0]
    sector_y = float(ys[keep].mean()) - origin[1]
    if sector_x == 0 and sector_y == 0:
        return marker_value, None, radius_a, origin_tuple
    sector_value = math.degrees(math.atan2(-sector_y, sector_x)) % 360
    return marker_value, sector_value, radius_a, origin_tuple
