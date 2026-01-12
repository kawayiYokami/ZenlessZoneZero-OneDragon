from __future__ import annotations

import threading
import time
from typing import Optional, TYPE_CHECKING

from one_dragon.base.conditional_operation.atomic_op import AtomicOp
from zzz_od.auto_battle.auto_battle_state import BattleStateEnum

if TYPE_CHECKING:
    from zzz_od.auto_battle.auto_battle_context import AutoBattleContext


class AtomicBtnMoveD(AtomicOp):

    def __init__(self, ctx: AutoBattleContext, press: bool = False, press_time: Optional[float] = None,
                 release: bool = False):
        if press:
            op_name = BattleStateEnum.BTN_MOVE_D.value + '按下'
        elif release:
            op_name = BattleStateEnum.BTN_MOVE_D.value + '松开'
        else:
            op_name = BattleStateEnum.BTN_MOVE_D.value
        AtomicOp.__init__(self, op_name=op_name, async_op=press and press_time is None)
        self.ctx: AutoBattleContext = ctx
        self.press: bool = press
        self.press_time: Optional[float] = press_time
        self.release: bool = release

        self._stop_event = threading.Event()

    def execute(self):
        self._stop_event.clear()

        if self.press and self.press_time is not None and self.press_time > 0:
            start_time = time.time()
            while time.time() - start_time < self.press_time:
                self.ctx.move_d(press=True)
                if self._stop_event.wait(0.02):
                    return

            if not self._stop_event.is_set():
                self.ctx.move_d(release=True)
        else:
            self.ctx.move_d(press=self.press, release=self.release)

    def stop(self):
        self._stop_event.set()
        if self.press:
            self.ctx.move_d(release=True)