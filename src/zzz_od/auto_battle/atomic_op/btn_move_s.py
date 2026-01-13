from __future__ import annotations

from typing import TYPE_CHECKING

from zzz_od.auto_battle.atomic_op.btn_base import AtomicBtnBase
from zzz_od.auto_battle.auto_battle_state import BattleStateEnum

if TYPE_CHECKING:
    from zzz_od.auto_battle.auto_battle_context import AutoBattleContext


class AtomicBtnMoveS(AtomicBtnBase):
    """移动S按钮操作"""

    def __init__(
        self,
        ctx: AutoBattleContext,
        press: bool = False,
        press_time: float | None = None,
        release: bool = False,
    ):
        """初始化移动S按钮操作

        Args:
            ctx: 自动战斗上下文
            press: 是否按下
            press_time: 按下时长（秒）
            release: 是否松开
        """
        AtomicBtnBase.__init__(
            self, ctx, BattleStateEnum.BTN_MOVE_S, press, press_time, release
        )

    def _execute_press(
        self, press: bool, press_time: float | None, release: bool
    ) -> None:
        """执行移动S操作

        Args:
            press: 是否按下
            press_time: 按下时长（秒）
            release: 是否松开
        """
        self.ctx.move_s(press=press, press_time=press_time, release=release)
