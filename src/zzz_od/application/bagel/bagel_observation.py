"""贝果导航使用的独立镜头朝向识别，不持有游戏输入。"""

from __future__ import annotations

from dataclasses import dataclass, fields
from math import isfinite
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import yaml

from one_dragon.utils import os_utils
from zzz_od.application.bagel.bagel_heading import HeadingObservation


@dataclass(frozen=True)
class ObservationSettings:
    """固定识别参数；运行中不改变，不属于用户导航配置。"""

    ring_inner_radius: int
    ring_outer_radius: int
    ring_min_mean_gradient: float
    ring_point_gradient: float
    ring_min_coverage: float
    camera_inner_radius: int
    camera_outer_radius: int
    sector_angle: int
    smooth_degrees: int
    yellow_floor: float
    yellow_ceiling: float
    color_balance_penalty: float
    background_percentile: float
    min_contrast: float
    min_concentration: float
    min_peak_separation: float
    min_sector_width: int
    max_sector_width: int
    max_radial_disagreement: float
    min_radial_contrast: float
    min_outer_radial_contrast_ratio: float
    min_outer_sector_width_ratio: float
    icon_min_yellow_contrast: float
    icon_max_area: int

    @classmethod
    def load(cls, path: Path | None = None) -> ObservationSettings:
        """读取参数文件并在处理图像前拒绝缺字段、非有限值和越界值。"""
        path = path or Path(
            os_utils.get_resource_path(
                'assets', 'game_data', 'bagel', 'observation.yml'
            )
        )
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        names = {field.name for field in fields(cls)}
        if not isinstance(data, dict) or set(data) != names:
            raise ValueError('观测参数必须包含完整且无多余的字段')
        return cls(**data)

    def __post_init__(self) -> None:
        """所有构造入口均校验参数的类型、尺寸与取值范围。"""
        data = {field.name: getattr(self, field.name) for field in fields(self)}
        if any(
            type(value) not in (int, float) or not isfinite(value)
            for value in data.values()
        ):
            raise ValueError('观测参数必须是有限数值，不能使用布尔值')
        integer_fields = {
            'ring_inner_radius',
            'ring_outer_radius',
            'camera_inner_radius',
            'camera_outer_radius',
            'sector_angle',
            'smooth_degrees',
            'min_sector_width',
            'max_sector_width',
            'icon_max_area',
        }
        if any(type(data[key]) is not int for key in integer_fields):
            raise ValueError('半径、角窗、平滑宽度和扇区宽度必须使用整数')
        settings = self
        if not (
            80 <= settings.ring_inner_radius < settings.ring_outer_radius <= 100
            and settings.ring_outer_radius - settings.ring_inner_radius >= 4
            and 0
            < settings.camera_inner_radius
            < settings.camera_outer_radius
            < settings.ring_inner_radius
            and settings.camera_outer_radius - settings.camera_inner_radius >= 6
            and 30 <= settings.sector_angle <= 150
            and 1 <= settings.smooth_degrees <= 21
            and settings.smooth_degrees % 2 == 1
            and 0 <= settings.yellow_floor < settings.yellow_ceiling <= 255
            and 0 <= settings.color_balance_penalty <= 2
            and 0 <= settings.background_percentile < 100
            and 0 < settings.min_sector_width < settings.max_sector_width < 180
            and 8 <= settings.icon_max_area <= 1000
            and 0 < settings.max_radial_disagreement < 90
            and all(
                0 < value <= 255
                for value in (
                    settings.ring_min_mean_gradient,
                    settings.ring_point_gradient,
                    settings.min_contrast,
                    settings.min_radial_contrast,
                    settings.icon_min_yellow_contrast,
                )
            )
            and all(
                0 < value <= 1
                for value in (
                    settings.ring_min_coverage,
                    settings.min_concentration,
                    settings.min_peak_separation,
                    settings.min_outer_radial_contrast_ratio,
                    settings.min_outer_sector_width_ratio,
                )
            )
        ):
            raise ValueError('观测参数越界或半径、扇区宽度的顺序错误')


@dataclass(frozen=True)
class MinimapVisibility:
    """圆框证据；不代表已确认地图布局或角色状态。"""

    visible: bool
    reason: str
    quality: dict[str, float]
    elapsed_ms: float


def _polar_maps(inner: int, outer: int) -> tuple[np.ndarray, np.ndarray]:
    """正右零度、顺时针增加；固定中心和标准小地图保持一致。"""
    angles = np.deg2rad(np.arange(360, dtype=np.float32))
    radii = np.arange(inner, outer + 1, dtype=np.float32)[:, None]
    return 100 + radii * np.cos(angles), 100 + radii * np.sin(angles)


def _circular_mean(values: np.ndarray, width: int) -> np.ndarray:
    """使用对称环绕窗口，避免零度被切断或偶数窗口造成半度偏移。"""
    half = width // 2
    offsets = range(-half, half + 1)
    return np.mean([np.roll(values, offset) for offset in offsets], axis=0)


def _angle_distance(first: float | np.ndarray, second: float) -> float | np.ndarray:
    """返回零到一百八十度的最小夹角。"""
    return np.abs((first - second + 180) % 360 - 180)


class BagelObserver:
    """从当前小地图读取镜头方向，不依赖地图、角色角度或历史结果。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        """固定本次识别参数，预计算采样坐标与径向权重。"""
        self.settings: ObservationSettings = ObservationSettings.load(settings_path)
        self._ring_maps: tuple[np.ndarray, np.ndarray] = _polar_maps(
            self.settings.ring_inner_radius,
            self.settings.ring_outer_radius,
        )
        self._camera_maps: tuple[np.ndarray, np.ndarray] = _polar_maps(
            self.settings.camera_inner_radius,
            self.settings.camera_outer_radius,
        )
        self._radians: np.ndarray = np.deg2rad(np.arange(360))
        y, x = np.ogrid[:201, :201]
        radius2 = (x - 100) ** 2 + (y - 100) ** 2
        self._icon_protected: np.ndarray = radius2 < self.settings.camera_inner_radius ** 2
        self._color_weight: np.ndarray = np.clip(
            (np.sqrt(radius2) - self.settings.camera_inner_radius)
            / (self.settings.camera_outer_radius - self.settings.camera_inner_radius),
            0, 1,
        ).astype(np.float32)
        self._camera_annulus: np.ndarray = (
            ~self._icon_protected & (radius2 <= self.settings.camera_outer_radius ** 2)
        )

    def camera_heading(self, crop: np.ndarray) -> HeadingObservation:
        """供导航读取本帧镜头；先检查圆框，不依赖角色朝向或定位结果。"""
        visibility = self._visibility(crop)
        if not visibility.visible:
            return HeadingObservation(None, visibility.reason, visibility.elapsed_ms, {})
        return self._camera_heading(crop)

    def _visibility(self, crop: np.ndarray) -> MinimapVisibility:
        """在外圈寻找跨角度一致的圆形边缘，不读取中心箭头。"""
        started = perf_counter()
        if crop.shape != (201, 201, 3) or crop.dtype != np.uint8:
            return MinimapVisibility(
                False, 'invalid_crop', {}, (perf_counter() - started) * 1000
            )
        settings = self.settings
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        polar = cv2.remap(gray, *self._ring_maps, cv2.INTER_LINEAR).astype(np.float32)
        gradient = np.abs(polar[2:] - polar[:-2])
        means = gradient.mean(axis=1)
        peak = int(np.argmax(means))
        near_peak = gradient[max(0, peak - 1) : peak + 2].max(axis=0)
        coverage = float(np.mean(near_peak >= settings.ring_point_gradient))
        quality = {
            'radius': float(settings.ring_inner_radius + 1 + peak),
            'mean_gradient': float(means[peak]),
            'coverage': coverage,
        }
        visible = (
            means[peak] >= settings.ring_min_mean_gradient
            and coverage >= settings.ring_min_coverage
        )
        return MinimapVisibility(
            bool(visible),
            'visible' if visible else 'minimap_not_visible',
            quality,
            (perf_counter() - started) * 1000,
        )

    def _response(self, polar: np.ndarray) -> np.ndarray:
        """径向平均后作角度平滑，再扣除角度背景。"""
        response = _circular_mean(polar.mean(axis=0), self.settings.smooth_degrees)
        return np.maximum(
            response - np.percentile(response, self.settings.background_percentile), 0
        )

    def _remove_icon_signal(self, yellow: np.ndarray) -> tuple[np.ndarray, int]:
        """屏蔽离开中心保护区的高亮小图标，保留箭头附近的真实扇区与大块背景。"""
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            (yellow >= self.settings.icon_min_yellow_contrast).astype(np.uint8), 8,
        )
        protected = set(np.unique(labels[self._icon_protected]))
        mask = np.zeros(yellow.shape, np.uint8)
        for index in range(1, count):
            if index not in protected and 8 <= stats[index, cv2.CC_STAT_AREA] <= self.settings.icon_max_area:
                mask[labels == index] = 1
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
        selected = mask.astype(bool) & self._camera_annulus
        cleaned = yellow.copy()
        cleaned[selected] = 0
        return cleaned, int(np.count_nonzero(selected))

    def _camera_heading(self, crop: np.ndarray) -> HeadingObservation:
        """从扇区自身决定方向和有效性，不接受角色角度或位置先验。"""
        started = perf_counter()
        settings = self.settings
        b, g, r = cv2.split(crop.astype(np.float32))
        yellow = np.maximum(np.minimum(r, g) - b, 0)
        yellow, masked_icon_pixels = self._remove_icon_signal(yellow)
        # 先用原色屏蔽图标，避免扣色后图标跌破门槛。内圈扇区较强，扣色由内向外渐增。
        yellow = np.maximum(
            yellow - settings.color_balance_penalty * self._color_weight * np.abs(r - g), 0,
        )
        polar = cv2.remap(yellow, *self._camera_maps, cv2.INTER_LINEAR)
        polar = np.clip(polar - settings.yellow_floor, 0, settings.yellow_ceiling)
        response = self._response(polar)
        scores = _circular_mean(response, settings.sector_angle)
        angle = int(np.argmax(scores))
        total = float(response.sum())
        peak = float(scores[angle])
        contrast = float(response.max())
        concentration = (
            float(abs(np.sum(response * np.exp(1j * self._radians))) / total)
            if total
            else 0.0
        )
        competitors = scores[
            _angle_distance(np.arange(360), angle) > settings.sector_angle
        ]
        separation = max(0.0, 1 - float(competitors.max()) / peak) if peak else 0.0
        width = int(np.count_nonzero(response >= contrast / 2)) if contrast else 0
        radial_angles: list[float] = []
        radial_contrasts: list[float] = []
        band_responses = [self._response(band) for band in np.array_split(polar, 3)]
        band_contrasts = [float(response.max()) for response in band_responses]
        # 最外组的真实扇区逐渐变淡；弱背景不能与内侧强扇区拥有相同否决作用。
        # 内侧两组仍使用原门槛，强背景同时污染中、外组时保留内组的冲突证据。
        outer_threshold = max(
            settings.min_radial_contrast,
            max(band_contrasts[:2]) * settings.min_outer_radial_contrast_ratio,
        )
        band_widths = [
            int(np.count_nonzero(band_response >= band_contrast / 2)) if band_contrast else 0
            for band_response, band_contrast in zip(band_responses, band_contrasts, strict=True)
        ]
        outer_width = band_widths[2]
        outer_width_threshold = max(
            settings.min_sector_width,
            min(band_widths[:2]) * settings.min_outer_sector_width_ratio,
        )
        for index, (band_response, band_contrast) in enumerate(zip(band_responses, band_contrasts, strict=True)):
            threshold = outer_threshold if index == 2 else settings.min_radial_contrast
            # 外圈局部图标即使超过绝对宽度，也不能冒充覆盖内圈同等范围的扇形。
            if index == 2 and outer_width < outer_width_threshold:
                continue
            if band_contrast >= threshold:
                radial_angles.append(
                    float(
                        np.argmax(_circular_mean(band_response, settings.sector_angle))
                    )
                )
                radial_contrasts.append(band_contrast)
        disagreement = max(
            (float(_angle_distance(item, angle)) for item in radial_angles),
            default=180.0,
        )
        quality = {
            'color_balance_penalty': settings.color_balance_penalty,
            'masked_icon_pixels': float(masked_icon_pixels),
            'yellow_contrast': contrast,
            'concentration': concentration,
            'peak_separation': separation,
            'sector_width': float(width),
            'radial_disagreement': disagreement,
            'radial_bands': float(len(radial_angles)),
            'radial_min_contrast': min(radial_contrasts, default=0.0),
            'outer_radial_contrast': band_contrasts[2],
            'outer_radial_threshold': outer_threshold,
            'outer_radial_width': float(outer_width),
            'outer_radial_width_threshold': float(outer_width_threshold),
        }
        reason = 'accepted'
        if contrast < settings.min_contrast:
            reason = 'low_sector_contrast'
        elif (
            concentration < settings.min_concentration
            or separation < settings.min_peak_separation
        ):
            reason = 'ambiguous_sector'
        elif not settings.min_sector_width <= width <= settings.max_sector_width:
            reason = 'invalid_sector_width'
        elif len(radial_angles) < 2 or disagreement > settings.max_radial_disagreement:
            reason = 'inconsistent_radii'
        return HeadingObservation(
            float(angle) if reason == 'accepted' else None,
            reason,
            (perf_counter() - started) * 1000,
            quality,
        )
