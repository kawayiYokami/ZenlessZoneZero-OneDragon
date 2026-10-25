"""贝果角色箭头方向；与镜头扇区和位置定位相互独立。"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot
from time import perf_counter
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class HeadingObservation:
    """有效角度与拒绝原因；质量量值不是正确概率。"""

    angle: float | None
    reason: str
    elapsed_ms: float
    quality: dict[str, float]


def character_heading(
    crop: np.ndarray,
    anchor: tuple[float, float],
    settings: Mapping[str, object],
) -> HeadingObservation:
    """沿用箭头亮色尖端算法；正右为零，顺时针增加。"""
    started = perf_counter()
    quality: dict[str, float] = {}

    def result(reason: str, angle: float | None = None) -> HeadingObservation:
        """为同一次识别附加耗时，不保存上一帧结果。"""
        return HeadingObservation(
            angle, reason, (perf_counter() - started) * 1000, quality
        )

    if crop.shape != (201, 201, 3) or crop.dtype != np.uint8:
        return result('invalid_crop')
    radius = int(settings['crop_radius'])
    ax, ay = (int(value) for value in anchor)
    if min(ax, ay) < radius or max(ax, ay) + radius >= 201:
        return result('invalid_anchor')
    arrow = crop[ay - radius : ay + radius + 1, ax - radius : ax + radius + 1]
    hsv = cv2.cvtColor(arrow, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv, np.array(settings['hsv_lower']), np.array(settings['hsv_upper'])
    )
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[np.ndarray] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not settings['area_range'][0] <= area <= settings['area_range'][1]:
            continue
        x, y, width, height = cv2.boundingRect(contour)
        if (
            x == 0
            or y == 0
            or x + width == mask.shape[1]
            or y + height == mask.shape[0]
        ):
            continue
        moments = cv2.moments(contour)
        if (
            hypot(moments['m10'] / area - radius, moments['m01'] / area - radius)
            <= settings['maximum_center_offset']
        ):
            candidates.append(contour)
    quality['candidate_count'] = float(len(candidates))
    if len(candidates) != 1:
        return result('arrow_missing' if not candidates else 'ambiguous_arrow')
    mask[:] = 0
    cv2.drawContours(mask, candidates, -1, 255, -1)
    ys, xs = np.nonzero(mask)
    green = arrow[:, :, 1]
    quality['green_spread'] = float(np.ptp(green[mask > 0]))
    if quality['green_spread'] < settings['minimum_green_spread']:
        return result('low_tip_contrast')
    threshold = np.percentile(green[mask > 0], settings['tip_percentile'])
    tip_y, tip_x = np.nonzero((mask > 0) & (green >= threshold))
    dx, dy = float(tip_x.mean() - xs.mean()), float(tip_y.mean() - ys.mean())
    quality['tip_offset'] = hypot(dx, dy)
    if quality['tip_offset'] < settings['minimum_tip_offset']:
        return result('weak_tip_offset')
    return result('accepted', degrees(atan2(dy, dx)) % 360)
