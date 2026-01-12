from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, ClassVar

from one_dragon.base.conditional_operation.atomic_op import AtomicOp
from one_dragon.base.conditional_operation.operation_def import OperationDef

if TYPE_CHECKING:
    from zzz_od.auto_battle.auto_battle_context import AutoBattleContext


class AtomicBtnQuickAssist(AtomicOp):

    OP_NAME: ClassVar[str] = '按键-快速支援'

    def __init__(self, ctx: AutoBattleContext, op_def: OperationDef):
        self.ctx: AutoBattleContext = ctx

        self.pre_delay: float = op_def.pre_delay
        self.post_delay: float = op_def.post_delay

        AtomicOp.__init__(self, op_name=AtomicBtnQuickAssist.OP_NAME)

        self._stop_event = threading.Event()

    def execute(self):
        self._stop_event.clear()

        if self.pre_delay > 0:
            self._stop_event.wait(self.pre_delay)
            if self._stop_event.is_set():
                return

        self.ctx.quick_assist()

        if self.post_delay > 0:
            self._stop_event.wait(self.post_delay)

    def stop(self) -> None:
        self._stop_event.set()