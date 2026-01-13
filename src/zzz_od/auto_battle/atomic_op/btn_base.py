from __future__ import annotations

import threading
import time
from abc import abstractmethod
from typing import TYPE_CHECKING

from one_dragon.base.conditional_operation.atomic_op import AtomicOp
from zzz_od.auto_battle.auto_battle_state import BattleStateEnum

if TYPE_CHECKING:
    from zzz_od.auto_battle.auto_battle_context import AutoBattleContext


class AtomicBtnBase(AtomicOp):
    """按钮操作的基类，提供通用的执行和中断机制"""

    def __init__(
        self,
        ctx: AutoBattleContext,
        btn_enum: BattleStateEnum,
        press: bool = False,
        press_time: float | None = None,
        release: bool = False,
    ):
        """初始化按钮操作

        Args:
            ctx: 自动战斗上下文
            btn_enum: 按钮枚举
            press: 是否按下
            press_time: 按下时长（秒）
            release: 是否松开
        """
        # 根据按键状态生成操作名称
        if press:
            op_name = btn_enum.value + "按下"
        elif release:
            op_name = btn_enum.value + "松开"
        else:
            op_name = btn_enum.value

        AtomicOp.__init__(self, op_name=op_name, async_op=press and press_time is None)
        self.ctx: AutoBattleContext = ctx
        self.press: bool = press
        self.press_time: float | None = press_time
        self.release: bool = release

        self._stop_event = threading.Event()

    @abstractmethod
    def _execute_press(
        self, press: bool, press_time: float | None, release: bool
    ) -> None:
        """子类实现具体的按键操作

        Args:
            press: 是否按下
            press_time: 按下时长（秒）
            release: 是否松开
        """
        pass

    def execute(self) -> None:
        """执行按钮操作，处理长按持续按下"""
        self._stop_event.clear()

        if self.press and self.press_time is not None and self.press_time > 0:
            # 长按持续按下
            start_time = time.time()
            while time.time() - start_time < self.press_time:
                self._execute_press(press=True, press_time=None, release=False)
                if self._stop_event.wait(0.02):
                    return
        else:
            # 普通按下/松开/点按
            self._execute_press(
                press=self.press, press_time=self.press_time, release=self.release
            )

    def stop(self) -> None:
        """中断执行"""
        self._stop_event.set()
        if self.press:
            self._execute_press(press=False, press_time=None, release=True)
