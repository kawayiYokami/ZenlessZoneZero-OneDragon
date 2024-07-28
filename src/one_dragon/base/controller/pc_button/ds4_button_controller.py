import time

from enum import Enum
from typing import Callable, List, Optional

from one_dragon.base.config.config_item import ConfigItem
from one_dragon.base.controller.pc_button import pc_button_utils
from one_dragon.base.controller.pc_button.pc_button_controller import PcButtonController


class Ds4ButtonEnum(Enum):

    CROSS = ConfigItem('X', 'ds4_0')
    CIRCLE = ConfigItem('○', 'ds4_1')
    SQUARE = ConfigItem('□', 'ds4_2')
    TRIANGLE = ConfigItem('△', 'ds4_3')
    L2 = ConfigItem('L2', 'ds4_4')
    R2 = ConfigItem('R2', 'ds4_5')
    L1 = ConfigItem('L1', 'ds4_6')
    R1 = ConfigItem('R1', 'ds4_7')


class Ds4ButtonController(PcButtonController):

    def __init__(self):
        PcButtonController.__init__(self)
        self.pad = None
        if pc_button_utils.is_vgamepad_installed():
            import vgamepad as vg
            self.pad = vg.VDS4Gamepad()
            self._btn = vg.DS4_BUTTONS

        self.handler: List[Callable[[Optional[float]], None]] = [
            self.press_a,
            self.press_b,
            self.press_x,
            self.press_y,
            self.press_lt,
            self.press_rt,
            self.press_lb,
            self.press_rb,
        ]

    def tap(self, key: str) -> None:
        """
        触发按键
        :param key:
        :return:
        """
        self.handler[int(key[-1])](None)

    def press_a(self, press_time: Optional[float] = None) -> None:
        self._press_button(self._btn.DS4_BUTTON_CROSS, press_time)

    def press_b(self, press_time: Optional[float] = None) -> None:
        self._press_button(self._btn.DS4_BUTTON_CIRCLE, press_time)

    def press_x(self, press_time: Optional[float] = None) -> None:
        self._press_button(self._btn.DS4_BUTTON_SQUARE, press_time)

    def press_y(self, press_time: Optional[float] = None) -> None:
        self._press_button(self._btn.DS4_BUTTON_TRIANGLE, press_time)

    def press_lt(self, press_time: Optional[float] = None) -> None:
        self.pad.left_trigger(value=255)
        self.pad.update()
        time.sleep(max(self.key_press_time, press_time))
        self.pad.left_trigger(value=0)
        self.pad.update()

    def press_rt(self, press_time: Optional[float] = None) -> None:
        self.pad.right_trigger(value=255)
        self.pad.update()
        time.sleep(max(self.key_press_time, press_time))
        self.pad.right_trigger(value=0)
        self.pad.update()

    def press_lb(self, press_time: Optional[float] = None) -> None:
        self._press_button(self._btn.DS4_BUTTON_SHOULDER_LEFT, press_time)

    def press_rb(self, press_time: Optional[float] = None) -> None:
        self._press_button(self._btn.DS4_BUTTON_SHOULDER_RIGHT, press_time)

    def _press_button(self, btn, press_time: Optional[float] = None):
        self.pad.press_button(btn)
        self.pad.update()
        time.sleep(max(self.key_press_time, press_time))
        self.pad.release_button(btn)
        self.pad.update()

    def reset(self):
        self.pad.reset()
        self.pad.update()

    def press(self, key: str, press_time: float) -> None:
        """
        :param key: 按键
        :param press_time: 持续按键时间
        :return:
        """
        self.handler[int(key[-1])](press_time)