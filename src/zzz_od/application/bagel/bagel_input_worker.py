"""贝果路线执行的输入线程：按键放到单独线程做，互不打断。

原来「按下并保持一段时间」是在主线里 time.sleep 阻塞的，
阻塞期间既不截图也不决策，走路就不跟手。搬到单独线程后主线投递即返回。

三条独立通道，各自有线程，互不打断、可并发：
- 前进（状态）：只记「该不该按着」，线程把真实键位对齐到意图，重复调用无副作用。
- 转向（互斥）：上一条没转完时，新请求直接忽略，不排队也不叠加。
  转向是相对位移，叠加会越转越过、一直转个不停。
- 即时动作（队列）：点按 F、长按 F、松开 F、闪避，按顺序执行。

三条通道互不占用，所以转向永远不会把前进顶掉（曾因此原地不动）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import TYPE_CHECKING

from one_dragon.utils.log_utils import log

if TYPE_CHECKING:
    from collections.abc import Callable

    from zzz_od.context.zzz_context import ZContext

TAP_INTERACT: str = 'tap_interact'
"""点按交互键（F），按满 duration 后松开。"""

HOLD_INTERACT: str = 'hold_interact'
"""按住交互键（F）不松，直到收到松开指令。"""

RELEASE_INTERACT: str = 'release_interact'
"""松开交互键。"""

DODGE: str = 'dodge'
"""点一下闪避，用于维持冲刺。"""

SLEEP_SLICE: float = 0.01
"""等待轮询的切片时长（秒）。"""

TURN_SETTLE: float = 0.08
"""转向下发后等画面更新的时长（秒）。

鼠标移动是瞬间返回的，但游戏把小地图朝向重新画出来要一帧多。
不等这一下，主线会在旧画面上截图，读到的角度还是转之前的。"""


class _Command:
    """一条待执行的按键指令。"""

    def __init__(
        self, kind: str, duration: float | None = None, angle: float = 0.0,
    ) -> None:
        """
        Args:
            kind: 指令类型，取本模块的常量。
            duration: 短按时长（秒），其它类型忽略。
            angle: 转向角度（度），仅转向使用。
        """
        self.kind: str = kind
        self.duration: float | None = duration
        self.angle: float = angle


class _InstantChannel:
    """即时动作通道：独立线程按顺序执行队列，互不打断。

    用于点按 F、长按 F、松开 F、闪避。这些动作很快，排队不会堆积，
    顺序执行更安全（比如先长按再松开）。
    """

    def __init__(self, name: str, handler: Callable[[_Command], None]) -> None:
        """
        Args:
            name: 线程名，便于排查。
            handler: 单条指令的执行函数。
        """
        self._handler: Callable[[_Command], None] = handler
        self._queue: deque[_Command] = deque()
        self._lock: threading.Lock = threading.Lock()
        self._wake: threading.Event = threading.Event()
        self._stopping: bool = False
        self._running: bool = False
        """正在执行一条指令；执行完才置回 False。"""
        self._thread: threading.Thread = threading.Thread(
            target=self._loop, name=name, daemon=True,
        )
        self._thread.start()

    def put(self, command: _Command) -> None:
        """入队一条指令。"""
        with self._lock:
            if self._stopping:
                return
            self._queue.append(command)
        self._wake.set()

    def busy(self) -> bool:
        """是否还有指令在排队或执行中。"""
        with self._lock:
            return self._running or bool(self._queue)

    def stop(self) -> None:
        """停止线程，丢掉未执行的指令。"""
        with self._lock:
            self._stopping = True
            self._queue.clear()
        self._wake.set()
        self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        """依次执行队列里的指令。"""
        while True:
            self._wake.wait(SLEEP_SLICE)
            self._wake.clear()
            while True:
                with self._lock:
                    if self._stopping:
                        return
                    if not self._queue:
                        break
                    command = self._queue.popleft()
                    self._running = True
                try:
                    self._handler(command)
                except Exception:
                    log.error('贝果即时动作执行失败', exc_info=True)
                finally:
                    with self._lock:
                        self._running = False


class _TurnChannel:
    """转向通道：同一时刻只执行一条，正在转时新请求直接忽略。

    转向是相对位移，排队会让它越积越多、一路转到过头；
    上一条没转完时又叠一条，还会拼命转。所以这里不是队列，
    而是「有在转就丢弃新请求」。

    转完还要再等一小段才放开 busy：鼠标事件下发是瞬间返回的，
    但游戏把新朝向画到小地图上要一帧多。不settle 的话，
    主线会在画面还没更新时截图，读到的还是转之前的朝向。
    """
    def __init__(self, handler: Callable[[float], None], settle: float = TURN_SETTLE) -> None:
        """
        Args:
            handler: 执行一次转向的函数，参数是角度。
            settle: 下发后等待画面更新的时长（秒）。
        """
        self._handler: Callable[[float], None] = handler
        self._settle: float = settle
        self._busy: bool = False
        self._pending: float | None = None
        self._lock: threading.Lock = threading.Lock()
        self._wake: threading.Event = threading.Event()
        self._stopping: bool = False
        self._thread: threading.Thread = threading.Thread(
            target=self._loop, name='bagel-turn', daemon=True,
        )
        self._thread.start()

    def submit(self, angle: float) -> bool:
        """请求一次转向；上一条还在转就忽略。

        Args:
            angle: 转向角度（度）。

        Returns:
            本次是否真的排上了。
        """
        with self._lock:
            if self._stopping or self._busy:
                return False
            self._busy = True
            self._pending: float | None = angle
        self._wake.set()
        return True

    def busy(self) -> bool:
        """是否有转向正在执行。"""
        with self._lock:
            return self._busy

    def stop(self) -> None:
        """停止线程。"""
        with self._lock:
            self._stopping = True
            self._pending = None
        self._wake.set()
        self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        """有请求就转一次，转完再放开。"""
        while True:
            self._wake.wait(SLEEP_SLICE)
            self._wake.clear()
            with self._lock:
                if self._stopping:
                    return
                angle = self._pending
                self._pending = None
            if angle is None:
                continue
            try:
                self._handler(angle)
                # 鼠标事件是瞬间返回的，但游戏把小地图朝向画出来要一帧多。
                # 等画面真的跟上再放开 busy，主线才不会读到转之前的旧角度。
                time.sleep(self._settle)
            except Exception:
                log.error('贝果转向执行失败', exc_info=True)
            finally:
                with self._lock:
                    self._busy = False


class BagelInputWorker:
    """路线执行的全部按键都经由这里下发，主线只投递不等待。"""

    def __init__(self, ctx: ZContext) -> None:
        """
        Args:
            ctx: 运行上下文，提供控制器。
        """
        self.ctx: ZContext = ctx
        self._forward_want: bool = False
        """主线意图：是否该一直按住前进。"""
        self._forward_down: bool = False
        """实际状态：W 键现在是否真的按着。两者不一致时才发键。"""
        self._forward_lock: threading.Lock = threading.Lock()
        self._stopping: bool = False

        self._turn_channel: _TurnChannel = _TurnChannel(self._run_turn)
        self._instant_channel: _InstantChannel = _InstantChannel(
            'bagel-instant', self._run_instant,
        )
        self._forward_thread: threading.Thread = threading.Thread(
            target=self._forward_loop, name='bagel-forward', daemon=True,
        )
        self._forward_thread.start()

    # ---------- 主线投递 ----------

    def hold_forward(self) -> None:
        """希望一直按住前进；线程会把它对齐到真实按键状态。"""
        with self._forward_lock:
            self._forward_want = True

    def stop_forward(self) -> None:
        """松开前进。"""
        with self._forward_lock:
            self._forward_want = False

    def step_forward(self, duration: float) -> None:
        """短按一次前进，按满时长自动松开。

        短按通过与按住同一条状态通道实现：先要求按住，到点再要求松开，
        所以它和转向也互不打断。

        Args:
            duration: 按住时长（秒）。
        """
        self.hold_forward()
        timer = threading.Timer(duration, self._release_after_step)
        timer.daemon = True
        timer.start()

    def _release_after_step(self) -> None:
        """短按到点：只有没被别人要求继续按住时才松开。"""
        with self._forward_lock:
            self._forward_want = False

    def turn(self, angle: float) -> None:
        """按角度转向；上一条还没转完就忽略本次。

        Args:
            angle: 转向角度（度），逆时针为正。
        """
        self._turn_channel.submit(angle)

    def idle(self) -> bool:
        """所有通道都空闲：转向没在转，即时动作也没有排队或执行中的。

        转向由线程异步下发，投递返回不代表转完。只有等到真正空闲时
        截到的画面才反映这次转向的结果，此刻读角度才可信。
        """
        return not self._turn_channel.busy() and not self._instant_channel.busy()

    def tap_interact(self, duration: float = 0.2) -> None:
        """点按交互键。

        Args:
            duration: 按住时长（秒）。
        """
        self._instant_channel.put(_Command(TAP_INTERACT, duration=duration))

    def hold_interact(self) -> None:
        """按住交互键不松，用于机械保险箱长按。"""
        self._instant_channel.put(_Command(HOLD_INTERACT))

    def release_interact(self) -> None:
        """松开交互键。"""
        self._instant_channel.put(_Command(RELEASE_INTERACT))

    def dodge(self) -> None:
        """点一下闪避，用于维持冲刺。"""
        self._instant_channel.put(_Command(DODGE))

    def shutdown(self) -> None:
        """停止全部通道并松开前进；退出路径必须调用。"""
        self._stopping = True
        with self._forward_lock:
            self._forward_want = False
        self._forward_thread.join(timeout=1.0)
        self._turn_channel.stop()
        self._instant_channel.stop()
        try:
            self.ctx.controller.move_w(release=True)
            self.ctx.controller.interact(release=True)
        except Exception:
            log.error('贝果输入线程退出时松开按键失败', exc_info=True)

    # ---------- 前进通道 ----------

    def _forward_loop(self) -> None:
        """每周期把前进键对齐到意图状态。

        检查、发键、写账本都在同一把锁内完成：即时通道的
        _ensure_forward_down 也会补按，两边若各自「先看后按」，
        会重复发一次按下，账本也可能被旧值覆盖。
        """
        while not self._stopping:
            with self._forward_lock:
                want = self._forward_want
                if want != self._forward_down:
                    try:
                        if want:
                            self.ctx.controller.move_w(press=True)
                        else:
                            self.ctx.controller.move_w(release=True)
                        self._forward_down = want
                    except Exception:
                        log.error('贝果输入线程切换前进键失败', exc_info=True)
            time.sleep(SLEEP_SLICE)

    # ---------- 转向通道 ----------

    def _run_turn(self, angle: float) -> None:
        """执行一次转向。

        Args:
            angle: 转向角度（度）。
        """
        self.ctx.controller.turn_by_angle_diff(angle)

    # ---------- 即时通道 ----------

    def _ensure_forward_down(self) -> None:
        """要走就先把前进键真的按下去，再让即时动作发出去。

        前进键是由 _forward_loop 每 10ms 对齐一次意图的，hold_forward()
        返回时键还没按下。此时立刻点闪避，游戏会先执行静止闪避（后跳或
        侧翻），再按前进就表现为「先退一下再走」，看起来像在按 S。
        所以发闪避这类动作之前，先把键位补齐。

        这里直接改 _forward_down，让账本和真实键位一致；否则 _forward_loop
        会以为已经按着，再也不补这一下。
        """
        with self._forward_lock:
            if not self._forward_want or self._forward_down:
                return
            try:
                self.ctx.controller.move_w(press=True)
                self._forward_down = True
            except Exception:
                log.error('贝果冲刺前补按前进键失败', exc_info=True)

    def _run_instant(self, command: _Command) -> None:
        """执行一条即时动作。

        Args:
            command: 要执行的指令。
        """
        controller = self.ctx.controller
        if command.kind == TAP_INTERACT:
            controller.interact(press=True)
            time.sleep(command.duration or 0.0)
            controller.interact(release=True)
            return
        if command.kind == HOLD_INTERACT:
            controller.interact(press=True)
            return
        if command.kind == RELEASE_INTERACT:
            controller.interact(release=True)
            return
        if command.kind == DODGE:
            # 冲刺是「按住前进再连点闪避」，顺序反了会变成静止闪避。
            self._ensure_forward_down()
            controller.dodge()
