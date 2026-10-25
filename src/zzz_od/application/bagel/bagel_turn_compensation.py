"""贝果任务内的转幅估计；只使用独立停车反馈，不写回通用配置。"""

from __future__ import annotations

import time
from collections import deque
from math import isfinite
from statistics import median
from typing import TYPE_CHECKING

from one_dragon.utils import cal_utils
from one_dragon.utils.log_utils import log
from zzz_od.operation.turning.turn_compensation import AngleTurnCompensator

if TYPE_CHECKING:
    from zzz_od.controller.zzz_pc_controller import ZPcController


class BagelTurnCompensator(AngleTurnCompensator):
    """累计可靠停车样本，保留同一任务、同输入条件下的转幅比例。"""

    _MIN_SAMPLES: int = 8
    _MAX_SCALE: float = 3.0
    _MOTION_SETTLE_SECONDS: float = 0.35
    _MAX_PROVISIONAL_SCALE: float = 1.5

    def __init__(self, controller: ZPcController) -> None:
        """创建任务级会话；初始输入仍沿用通用控制器的基础换算。"""
        super().__init__(controller)
        self._samples: deque[float] = deque(maxlen=24)
        self._recent_ratios: deque[float] = deque(maxlen=3)
        self._calibrated: bool = False
        self._commands_since_sample: int = 0
        self._last_motion_at: float = float('-inf')
        self._conditions: tuple[float, bool, int, int] | None = None

    def reset(self) -> None:
        """清空已学比例与样本；调用方另外维护实际输入条件。"""
        super().reset()
        self._samples.clear()
        self._recent_ratios.clear()
        self._calibrated = False
        self._commands_since_sample = 0

    @property
    def calibrated(self) -> bool:
        """返回是否已经由多组有效反馈建立比例。"""
        return self._calibrated

    @property
    def needs_sample(self) -> bool:
        """定期保留完整停车反馈，避免快速修正后长期不再核对转幅。"""
        return not self._calibrated or self._commands_since_sample >= 6

    def turn(self, angle_diff: float, max_abs_angle_diff: float | None = None) -> float:
        """保留原单次输入上限，并记录距离上次有效样本的命令数。"""
        commanded = super().turn(angle_diff, max_abs_angle_diff)
        self._commands_since_sample += 1
        return commanded

    def check_conditions(self) -> None:
        """输入模式、基础换算或窗口尺寸改变时重新估计。"""
        rect = self.controller.game_win.win_rect
        conditions = None if rect is None else (
            self.controller.game_config.turn_dx,
            self.controller.background_mode,
            rect.width,
            rect.height,
        )
        if conditions != self._conditions:
            self.reset()
            self._conditions = conditions
            log.info('贝果转幅估计：输入条件变更，清空比例；条件=%s', conditions)

    def note_motion(self) -> None:
        """记录前进仍在生效或刚结束，避免把运动中的反馈用于标定。"""
        self._last_motion_at = time.monotonic()

    def can_learn(self) -> bool:
        """只有停止前进一段时间后，才接纳新的停车命令作为样本。"""
        return time.monotonic() - self._last_motion_at >= self._MOTION_SETTLE_SECONDS

    def _adjust_provisional_scale(self) -> None:
        """完整标定未收敛时，只根据连续三组一致欠转反馈小幅增加输入。"""
        if len(self._recent_ratios) < self._recent_ratios.maxlen:
            return
        ratios = list(self._recent_ratios)
        estimate = self.scale
        if min(ratios) > self.scale * 1.2:
            estimate = min(min(ratios) * 0.9, self._MAX_PROVISIONAL_SCALE)
        elif max(ratios) < self.scale * 0.9:
            estimate = max(ratios)
        previous = self.scale
        change = max(-self._MAX_SCALE_CHANGE, min(estimate - previous, self._MAX_SCALE_CHANGE))
        self.scale = max(self._MIN_SCALE, min(previous + change, self._MAX_PROVISIONAL_SCALE))
        if abs(self.scale - previous) >= 0.01:
            log.info('贝果临时转幅：连续有效反馈=%s，比例 %.3f -> %.3f；尚未完成标定',
                     ratios, previous, self.scale)

    def learn(self, source_angle: float, effective_angle_diff: float, current_angle: float) -> None:
        """用多组同方向、足够幅度的停车反馈估计名义命令与观测转幅之比。"""
        observed = cal_utils.angle_delta(source_angle, current_angle)
        if (
            not all(isfinite(value) for value in (source_angle, effective_angle_diff, current_angle))
            or not 15 <= abs(effective_angle_diff) <= 90
            or not 5 <= abs(observed) <= 120
            or observed * effective_angle_diff <= 0
        ):
            return
        ratio = effective_angle_diff / observed
        if not 0.5 <= ratio <= 4:
            return
        self._commands_since_sample = 0
        self._recent_ratios.append(ratio)
        if self._calibrated and len(self._recent_ratios) == self._recent_ratios.maxlen:
            ratios = list(self._recent_ratios)
            if all(value > self.scale * 1.35 for value in ratios) or all(
                value < self.scale * 0.65 for value in ratios
            ):
                log.warning('贝果转幅估计：连续反馈偏离已学比例，重新估计；原比例=%.3f，样本=%s',
                            self.scale, ratios)
                self.reset()
                self._samples.extend(ratios)
                return
        self._samples.append(ratio)
        estimate = median(self._samples)
        spread = median(abs(value - estimate) for value in self._samples) / estimate
        log.debug('贝果转幅样本：命令=%.1f，观测=%.1f，比例=%.3f，数量=%s，中位数=%.3f，偏差=%.3f',
                  effective_angle_diff, observed, ratio, len(self._samples), estimate, spread)
        if len(self._samples) < self._MIN_SAMPLES or spread > 0.15:
            if not self._calibrated:
                self._adjust_provisional_scale()
            return
        previous = self.scale
        if self._calibrated:
            estimate = previous + max(-self._MAX_SCALE_CHANGE, min(estimate - previous, self._MAX_SCALE_CHANGE))
        self.scale = max(self._MIN_SCALE, min(estimate, self._MAX_SCALE))
        self._calibrated = True
        if abs(previous - self.scale) >= 0.01:
            log.info('贝果转幅估计：有效样本=%s，比例 %.3f -> %.3f，样本相对偏差=%.3f',
                     len(self._samples), previous, self.scale, spread)
