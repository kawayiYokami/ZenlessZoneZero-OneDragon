"""小地图镜头朝向识别。

流程：
1. 按 HSV 范围过滤切出亮区，再做腐蚀、膨胀把圆心菱形和外面扇形断开。
   参数对应流水线 assets/image_analysis_pipelines/小地图方向识别.yml。
2. 镜头朝向：圆心固定用手标真值（几何中心偏 2.8、1.5），即角色位置，
   不再检测菱形；在固定半径的环里找扇形两侧边，取角平分线。
   环带内径 18 大于菱形半径，标记自身本来就不在环里。

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

SECTOR_CENTER_OFFSET: tuple[float, float] = (2.8, 1.5)
"""镜头圆心相对几何中心的偏移。红豆手标六点拟合：201 图上真圆心
(102.8, 101.5)，几何中心 (100, 100)。线上裁剪同为 201，直接套偏移。"""

SECTOR_INNER_RADIUS: int = 18
SECTOR_OUTER_RADIUS: int = 40
"""求扇形方向的环形带，单位像素，以固定圆心为准。

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
        center_x: 圆心 x，取固定圆心。
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


def analyze(
    rgb: MatLike,
) -> tuple[float | None, float | None, float | None, float, tuple[float, float]]:
    """只算镜头朝向与固定圆心，不再检测菱形。

    圆心即角色位置，直接用手标固定值。返回保持 5 元组，
    角色朝向两项恒为 None、半径恒为 0.0，调用方解包不用改。

    Args:
        rgb: 小地图 RGB 图，正方形。

    Returns:
        (None, None, 镜头朝向, 0.0, 圆心)。
        扫不到可信扇形时镜头朝向为 None。圆心为手标固定圆心。
    """
    height, width = rgb.shape[:2]
    geo_x, geo_y = (width - 1) / 2, (height - 1) / 2
    center_x = geo_x + SECTOR_CENTER_OFFSET[0]
    center_y = geo_y + SECTOR_CENTER_OFFSET[1]
    center = (center_x, center_y)

    mask = _bright_mask(rgb)
    if int(mask.sum()) < MIN_PIXELS:
        return None, None, None, 0.0, center

    # 环带内径大于菱形半径，标记自身不在环里，不用清掉也能直接扫。
    sector_value = _sector_angle(mask, center_x, center_y)
    return None, None, sector_value, 0.0, center
