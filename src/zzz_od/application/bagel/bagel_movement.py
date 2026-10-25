"""贝果导航持有的移动键；前台键盘支持镜头相对八方向。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from one_dragon.base.controller.pc_button import pc_button_utils
from one_dragon.utils.log_utils import log

if TYPE_CHECKING:
    from one_dragon.base.controller.pc_button.pc_button_controller import (
        PcButtonController,
    )
    from zzz_od.controller.zzz_pc_controller import ZPcController


class BagelMovement:
    """记录实际发送的后端和键位，统一处理换向、短步及结束释放。"""

    DIRECTIONS: tuple[tuple[str, ...], ...] = (
        ('w',), ('w', 'a'), ('a',), ('s', 'a'),
        ('s',), ('s', 'd'), ('d',), ('w', 'd'),
    )

    def __init__(self, controller: ZPcController) -> None:
        """只管理本导航发出的移动键，不重置控制器的全部按键。"""
        self.controller: ZPcController = controller
        self.sector: int | None = None
        self._held: dict[str, tuple[PcButtonController, str]] = {}

    @property
    def supports_directions(self) -> bool:
        """当前手柄方向接口会覆盖整根摇杆，因此八方向只用于前台键盘。"""
        return (
            not self.controller.background_mode
            and self.controller.btn_controller is self.controller.keyboard_controller
        )

    @property
    def moving(self) -> bool:
        """侧移或后退也属于正在移动，不以控制器的前进标志代替。"""
        return bool(self._held) or self.controller.is_moving

    def _release_keys(self, keys: tuple[str, ...]) -> None:
        """逐键释放；一个后端异常也继续尝试释放其余已持有键。"""
        errors: list[Exception] = []
        for logical in keys:
            owned = self._held.get(logical)
            if owned is None:
                continue
            backend, physical = owned
            try:
                try:
                    backend.release(physical)
                finally:
                    if backend is self.controller.keyboard_controller:
                        # 按下或释放中途出错时，仍向原设备发送该键的释放。
                        if pc_button_utils.is_mouse_button(physical):
                            backend.mouse.release(pc_button_utils.get_mouse_button(physical))
                        else:
                            backend.keyboard.release(pc_button_utils.get_keyboard_button(physical))
                self._held.pop(logical)
            except Exception as error:
                errors.append(error)
                log.error('贝果移动键释放失败：%s', logical, exc_info=True)
        self.controller.is_moving = 'w' in self._held
        if errors:
            raise RuntimeError('贝果移动键未能全部释放') from errors[0]

    def hold(self, sector: int) -> None:
        """保持选定组合；共同键不松开，新键无定时按下，由本类负责释放。"""
        if sector not in range(len(self.DIRECTIONS)):
            raise ValueError('无效的移动方向')
        if sector != 0 and not self.supports_directions:
            raise ValueError('当前输入后端不支持八方向键盘移动')
        if not self.controller.background_mode and not self.controller.game_win.is_win_active:
            self.stop()
            raise RuntimeError('游戏不在前台，已停止导航移动')
        backend = self.controller.btn_controller
        if any(
            owner is not backend or physical != self.controller.action_keys[f'move_{logical}']
            for logical, (owner, physical) in self._held.items()
        ):
            self.stop()
        desired = self.DIRECTIONS[sector]
        try:
            self._release_keys(tuple(logical for logical in self._held if logical not in desired))
            for logical in desired:
                if logical in self._held:
                    continue
                physical = self.controller.action_keys[f'move_{logical}']
                self._held[logical] = (backend, physical)
                backend.press(physical)
            if sector != self.sector:
                log.debug('贝果移动按键：%s', '+'.join(desired))
            self.sector = sector
            self.controller.is_moving = 'w' in self._held
        except Exception:
            log.error('贝果移动按键更新失败', exc_info=True)
            self.stop()
            raise

    def stop(self) -> None:
        """释放本导航的全部方向键，不因后端切换遗留旧设备上的输入。"""
        try:
            self._release_keys(tuple(self._held))
        finally:
            self.sector = None

    def start_sprint(self, seconds: float) -> bool:
        """保持移动方向并短按一次闪避键；异常和外部暂停时也向原后端释放。"""
        if not self.supports_directions or self.sector is None or not self._held:
            return False
        if not self.controller.game_win.is_win_active:
            self.stop()
            raise RuntimeError('游戏不在前台，取消冲刺起步')
        backend = self.controller.keyboard_controller
        physical = self.controller.action_keys['dodge']
        if any(key == physical for _, key in self._held.values()):
            return False
        try:
            backend.press(physical)
            time.sleep(seconds)
        finally:
            try:
                backend.release(physical)
            finally:
                if pc_button_utils.is_mouse_button(physical):
                    backend.mouse.release(pc_button_utils.get_mouse_button(physical))
                else:
                    backend.keyboard.release(pc_button_utils.get_keyboard_button(physical))
        return True

    def pulse(self, sector: int, seconds: float) -> None:
        """组合同时保持指定时长；正常结束和异常都执行收键。"""
        try:
            self.hold(sector)
            time.sleep(seconds)
        finally:
            self.stop()
