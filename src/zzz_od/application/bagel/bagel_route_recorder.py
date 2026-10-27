"""贝果路线打点录制：按数字键打不同类型的点，按退格删掉上一个点。

打点只记坐标与所在分层，不记动作。走到点上该做什么，等回放到那个点再识别交互物决定，
所以旧机制里「先 interact 再 unlock 再 store」这套写死的顺序不再存在。

按键沿用插件那套：
- 1 途径点、2 电子保险箱、3 机械保险箱、4 普通箱子、5 门、6 电梯，按下即在当前位置打一个点。
- 退格删掉上一个点。
"""

from __future__ import annotations

import ctypes
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any

import numpy as np
from pynput import keyboard

from one_dragon.utils import cv2_utils, os_utils
from one_dragon.utils.log_utils import log
from zzz_od.game_data.map_locator.map_locator import MultiMapLocator, load_map_locators
from zzz_od.game_data.map_locator.minimap_angle import analyze

if TYPE_CHECKING:
    from collections.abc import Callable

    from cv2.typing import MatLike

    from one_dragon.base.geometry.rectangle import Rect
    from zzz_od.context.zzz_context import ZContext

ROUTE_VERSION = 1
"""路线文件格式版本。字段含义变化时递增。"""

ROUTE_DIR_NAME = 'bagel_routes'
"""路线文件目录，位于工作目录下，不进版本库。"""

MINIMAP_AREA = ('贝果-局内', '定位小地图')
"""小地图裁剪区域，与导航共用同一份建档。"""

FOREGROUND_CHECK_INTERVAL = 0.2
"""检查游戏是否仍在前台的间隔（秒）。"""

SPEED_WINDOW_SECONDS = 1.0
"""单帧最大偏移的统计窗口（秒）。"""

SHAKE_TOLERANCE = 1
"""与上一帧坐标差在这个像素内视为原地抖动，沿用上一帧坐标。"""

FOREGROUND_WAIT_SECONDS = 3.0
"""切到游戏前台的最长等待秒数。"""

POINT_MIN_DISTANCE = 8.0
"""同类型点与上一个点的最小间距；原地重复按不产生新点。"""

UNDO_KEY = 'backspace'
"""删除上一个点的按键。"""

POINT_KINDS: dict[str, tuple[str, str]] = {
    '1': ('beacon', '途径点'),
    '2': ('electronic_safe', '电子保险箱'),
    '3': ('mechanical_safe', '机械保险箱'),
    '4': ('box', '普通箱子'),
    '5': ('door', '门'),
    '6': ('elevator', '电梯'),
}
"""打点键到（类型标识, 中文名）的映射。"""

ELEVATOR: str = 'elevator'
"""电梯类型标识。进门后可能出楼层选项，也可能不出。"""

ELEVATOR_EXIT_FORWARD: str = 'forward'
ELEVATOR_EXIT_BACKWARD: str = 'backward'
"""电梯出来时朝哪个方向走。"""

KIND_ALIASES: dict[str, str] = {
    'briefcase': 'box',
}
"""旧路线文件里的类型名到当前类型名；改名后仍能读回。"""

KIND_LABELS: dict[str, str] = dict(POINT_KINDS.values())
"""类型标识到中文名，供界面与网页显示。"""

KIND_COLORS: dict[str, str] = {
    'beacon': '#2233b4',
    'electronic_safe': '#d97400',
    'mechanical_safe': '#8b3fc4',
    'box': '#1f8a4c',
    'door': '#238495',
    'elevator': '#bc2f5e',
}
"""类型标识到标注色，与网页画点用的颜色是同一套。

电子=橙、机械=紫、普通箱=绿是语义色，其余三类按色相环拉开，
任意两类的色相至少差 41 度，肉眼可辨。
"""


@dataclass(frozen=True)
class BagelRoutePoint:
    """一个打点：全局坐标、所在分层与类型。

    点按打点顺序排列，顺序本身就是路线，不需要额外的连线数据。
    电梯点额外带两项进出电梯的参数。
    """

    x: float
    y: float
    layer: str
    kind: str
    elevator_option: str = ''
    """电梯要选的楼层文字，如「负二层站台」；空表示不选（直接进门）。"""
    elevator_exit: str = 'forward'
    """出电梯往哪个方向走：forward 前进、backward 后退。"""

    def to_dict(self) -> dict[str, Any]:
        """生成可写入 JSON 的点。"""
        data: dict[str, Any] = {
            'x': round(self.x),
            'y': round(self.y),
            'layer': self.layer,
            'kind': self.kind,
        }
        if self.kind == ELEVATOR:
            data['elevator_option'] = self.elevator_option
            data['elevator_exit'] = self.elevator_exit
        return data


class BagelRoute:
    """一条打点路线：有序点位，顺序即路径。"""

    def __init__(self, route_id: str) -> None:
        """
        Args:
            route_id: 路线标识，同时用作文件名。
        """
        self.route_id: str = route_id
        self.started_at: str = datetime.now().astimezone().isoformat(timespec='seconds')
        self.ended_at: str | None = None
        self.enabled: bool = True
        """是否启用：关闭的路线执行时当作不存在，编辑器里照旧显示。"""
        self.points: list[BagelRoutePoint] = []

    def append_point(self, point: BagelRoutePoint) -> bool:
        """追加一个点；同类型且与上一个点太近时忽略。

        Args:
            point: 待追加的点。

        Returns:
            真正加入时返回 True。
        """
        if self.points:
            last = self.points[-1]
            if (
                last.kind == point.kind
                and last.layer == point.layer
                and ((last.x - point.x) ** 2 + (last.y - point.y) ** 2)
                < POINT_MIN_DISTANCE ** 2
            ):
                return False
        self.points.append(point)
        return True

    def pop_point(self) -> BagelRoutePoint | None:
        """删掉最后一个点。

        Returns:
            被删掉的点；没有点时返回 None。
        """
        if not self.points:
            return None
        return self.points.pop()

    def to_dict(self) -> dict[str, Any]:
        """生成待写入的数据。"""
        return {
            'version': ROUTE_VERSION,
            'route_id': self.route_id,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'enabled': self.enabled,
            'points': [point.to_dict() for point in self.points],
        }

    def save(self, path: Path) -> Path:
        """写入并读回核对点数。

        Args:
            path: 目标文件路径。

        Returns:
            实际写入的路径。

        Raises:
            ValueError: 读回点数与内存不一致。
            OSError: 写盘失败。
        """
        if self.ended_at is None:
            self.ended_at = datetime.now().astimezone().isoformat(timespec='seconds')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding='utf-8',
        )
        reloaded = BagelRoute.load(path)
        if len(reloaded.points) != len(self.points):
            raise ValueError(
                f'路线文件读回点数不一致：写入 {len(self.points)}，读回 {len(reloaded.points)}'
            )
        return path

    @classmethod
    def load(cls, path: Path) -> BagelRoute:
        """读回一份路线文件。

        Args:
            path: 路线文件路径。

        Returns:
            还原后的路线。

        Raises:
            ValueError: 版本不支持或数据不完整。
        """
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != ROUTE_VERSION:
            raise ValueError(f'不支持的路线文件版本：{data.get("version")}')
        route = cls(str(data['route_id']))
        route.started_at = data['started_at']
        route.ended_at = data.get('ended_at')
        route.enabled = bool(data.get('enabled', True))
        # 旧文件没有 enabled 字段，按启用处理。
        route.points = [
            BagelRoutePoint(
                float(round(item['x'])), float(round(item['y'])), str(item['layer']),
                KIND_ALIASES.get(str(item['kind']), str(item['kind'])),
                elevator_option=str(item.get('elevator_option', '')),
                elevator_exit=str(item.get('elevator_exit', ELEVATOR_EXIT_FORWARD)),
            )
            for item in data['points']
        ]
        return route


def route_dir() -> Path:
    """返回路线文件目录，不存在时创建。

    Returns:
        工作目录下的路线目录。
    """
    directory = Path(os_utils.get_work_dir()) / ROUTE_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_routes(enabled_only: bool = False) -> list[BagelRoute]:
    """读出全部路线，损坏的文件跳过并记日志。

    Args:
        enabled_only: 只返回启用中的路线；执行侧用它把关闭的路线当作不存在。

    Returns:
        按开始时间排列的路线列表。
    """
    routes: list[BagelRoute] = []
    for path in sorted(route_dir().glob('*.route.json')):
        try:
            route = BagelRoute.load(path)
        except (OSError, ValueError, KeyError) as error:
            log.warning('跳过损坏的贝果路线文件 %s：%s', path.name, error)
            continue
        if enabled_only and not route.enabled:
            continue
        routes.append(route)
    return routes


def set_route_enabled(route_id: str, enabled: bool) -> bool:
    """开关一条路线并写回文件。

    Args:
        route_id: 路线标识。
        enabled: True 启用、False 关闭。

    Returns:
        真的改动了文件时返回 True；文件不存在或状态未变返回 False。

    Raises:
        OSError: 写盘失败。
    """
    path = route_dir() / f'{route_id}.route.json'
    if not path.is_file():
        return False
    route = BagelRoute.load(path)
    if route.enabled == enabled:
        return False
    route.enabled = enabled
    route.save(path)
    return True


def delete_route(route_id: str) -> bool:
    """删除一条路线的文件。

    Args:
        route_id: 路线标识，同时是文件名主干。

    Returns:
        真正删掉文件时返回 True；文件不存在返回 False。
    """
    path = route_dir() / f'{route_id}.route.json'
    if not path.is_file():
        return False
    path.unlink()
    return True


ILLEGAL_NAME_CHARS = '\\/:*?"<>|'
"""文件名里不能出现的字符，与 Windows 限制一致。"""


def rename_route(route_id: str, new_id: str) -> str:
    """把一条路线改名，同时改文件名。

    路线标识就是文件名主干，所以改名等于改文件。改名后旧标识立即失效。

    Args:
        route_id: 原路线标识。
        new_id: 新路线标识。

    Returns:
        实际使用的新标识。

    Raises:
        ValueError: 新名字非法、原文件不存在或目标已存在。
        OSError: 改名失败。
    """
    new_id = new_id.strip()
    if not new_id:
        raise ValueError('名字不能为空')
    if any(ch in new_id for ch in ILLEGAL_NAME_CHARS):
        raise ValueError(f'名字不能含这些字符：{ILLEGAL_NAME_CHARS}')
    if new_id in ('.', '..'):
        raise ValueError('名字不合法')
    old_path = route_dir() / f'{route_id}.route.json'
    new_path = route_dir() / f'{new_id}.route.json'
    if not old_path.is_file():
        raise ValueError(f'路线不存在：{route_id}')
    if new_path.exists():
        raise ValueError(f'已经有同名路线：{new_id}')
    route = BagelRoute.load(old_path)
    route.route_id = new_id
    route.save(new_path)
    old_path.unlink()
    return new_id


def is_foreground(hwnd: int) -> bool:
    """判断窗口是否为当前前台窗口。

    Args:
        hwnd: 窗口句柄。

    Returns:
        前台时为 True。
    """
    return int(ctypes.windll.user32.GetForegroundWindow()) == int(hwnd)


class BagelPointRecorder:
    """打点录制器；调用方在后台线程里跑 ``run``，用回调拿状态与点。

    逐帧定位持续运行，只有按下数字键才打点，退格删掉上一个点。
    """

    def __init__(
        self,
        ctx: ZContext,
        route_id: str,
        on_point: Callable[[BagelRoutePoint], None] | None = None,
        on_undo: Callable[[], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_layer: Callable[[str, Path], None] | None = None,
        on_position: Callable[[tuple[float, float] | None], None] | None = None,
        on_angles: Callable[[float | None, float | None], None] | None = None,
        on_stats: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """
        Args:
            ctx: 已初始化的上下文。
            route_id: 本次录制的路线标识。
            on_point: 每打下一个点回调一次。
            on_undo: 每删掉一个点回调一次。
            on_status: 阶段变化回调。
            on_layer: 命中层变化时回调，参数为分层名与底图路径。
            on_position: 每帧回调层内坐标，定位失败时为 None。
            on_angles: 每帧回调（镜头朝向, 角色朝向），识别不出时为 None。
            on_stats: 每帧回调定位统计，字段见 ``_build_stats``。
        """
        self.ctx: ZContext = ctx
        self.route_id: str = route_id
        self.on_point: Callable[[BagelRoutePoint], None] = on_point or (lambda _p: None)
        self.on_undo: Callable[[], None] = on_undo or (lambda: None)
        self.on_status: Callable[[str], None] = on_status or (lambda _t: None)
        self.on_layer: Callable[[str, Path], None] = on_layer or (lambda _n, _p: None)
        self.on_position: Callable[[tuple[float, float] | None], None] = (
            on_position or (lambda _c: None)
        )
        self.on_angles: Callable[[float | None, float | None], None] = (
            on_angles or (lambda _s, _m: None)
        )
        self.on_stats: Callable[[dict[str, Any]], None] = on_stats or (lambda _s: None)
        self.stop_requested: Event = Event()
        self.route: BagelRoute = BagelRoute(route_id)
        self.saved_path: Path | None = None
        self.failure: str | None = None
        self.stop_reason: str = ''

        self._pending: list[str] = []
        self._listener: keyboard.Listener | None = None
        self._locator: MultiMapLocator | None = None
        self._layer_paths: dict[str, Path] = {}
        self._shown_layer: str | None = None
        self._shown_center: tuple[int, int] | None = None
        self._track: list[tuple[float, tuple[int, int]]] = []
        self._frame_count: int = 0
        self._fps: float = 0.0
        self._fps_start: float = 0.0

    def request_stop(self) -> None:
        """请求停止，收尾由 ``run`` 自己完成。"""
        self.stop_requested.set()

    def run(self) -> None:
        """持续定位，按数字键打点、按退格删掉上一个点。"""
        controller = self.ctx.controller
        if controller is None or not controller.init_game_win():
            self.failure = '游戏窗口未就绪，请先启动游戏'
            return
        game_win = controller.game_win
        hwnd = game_win.get_hwnd()
        if hwnd is None or not controller.is_game_window_ready:
            self.failure = '未找到游戏窗口，请先启动游戏'
            return
        area = self.ctx.screen_loader.get_area(*MINIMAP_AREA)
        if area is None:
            self.failure = f'缺少画面区域：{MINIMAP_AREA[0]}/{MINIMAP_AREA[1]}'
            return

        self.route = BagelRoute(self.route_id)
        try:
            self.on_status('正在载入底图')
            locators = load_map_locators()
            self._locator = MultiMapLocator(locators)
            self._layer_paths = {locator.name: locator.image_path for locator in locators}
            self.on_status('准备切到游戏')
            controller.active_window()
            if not self._wait_for_foreground(hwnd):
                self.failure = '游戏窗口未切到前台，录制未开始'
                return
            self._start_key_listener()
            self.on_status('录制中，1-6 打点，退格删上一个点')
            self.stop_reason = self._sample_loop(hwnd, area.pc_rect)
        except Exception as error:
            log.error('贝果路线打点异常', exc_info=True)
            self.failure = f'录制异常：{error}'
            return
        finally:
            self._stop_key_listener()

        if not self.route.points:
            self.failure = '没有打下任何点，未生成文件'
            return
        try:
            self.saved_path = self.route.save(route_dir() / f'{self.route_id}.route.json')
        except (OSError, ValueError) as error:
            log.error('贝果路线打点保存失败', exc_info=True)
            self.failure = f'保存失败：{error}'
            return
        self.on_status(self.stop_reason)

    def _sample_loop(self, hwnd: int, rect: Rect) -> str:
        """定位循环，只在手动停止时结束；切出游戏时暂停定位但不停止录制。

        Args:
            hwnd: 游戏窗口句柄。
            rect: 小地图区域。

        Returns:
            停止原因文字。
        """
        controller = self.ctx.controller
        last_foreground_check = 0.0
        foreground = True
        while not self.stop_requested.is_set():
            now = time.monotonic()
            if now - last_foreground_check >= FOREGROUND_CHECK_INTERVAL:
                last_foreground_check = now
                foreground = is_foreground(hwnd)
                if not foreground:
                    # 切出游戏时游戏画面可能被遮挡，暂停采样，回到游戏继续。
                    self.on_position(None)
            if not foreground:
                time.sleep(FOREGROUND_CHECK_INTERVAL)
                continue
            screenshot_at = time.time()
            screenshot = controller.get_screenshot()
            if screenshot is None:
                continue
            crop = self._crop(screenshot, rect)
            if crop is None:
                continue
            angle_start = time.perf_counter()
            marker, _marker_edges, sector, _radius, _origin = analyze(crop)
            angle_ms = (time.perf_counter() - angle_start) * 1000
            self.on_angles(sector, marker)
            locate_start = time.perf_counter()
            located = self._locate(crop)
            locate_ms = (time.perf_counter() - locate_start) * 1000
            if located is None:
                self.on_position(None)
                self.on_stats(self._build_stats(None, None, None, angle_ms, locate_ms, 0.0))
                continue
            locator, score, center = located
            center = self._debounce(locator.name, center)
            total_ms = (time.time() - screenshot_at) * 1000
            local = (
                float(center[0] - locator.origin[0]),
                float(center[1] - locator.origin[1]),
            )
            if locator.name != self._shown_layer:
                image_path = self._layer_paths.get(locator.name)
                if image_path is None:
                    self.on_position(None)
                    continue
                self._shown_layer = locator.name
                self.on_layer(locator.name, image_path)
            self.on_position(local)
            step_max, window_span = self._track_step(center)
            self.on_stats(
                self._build_stats(
                    locator.name, center, score, angle_ms, locate_ms, total_ms,
                    step_max, window_span,
                ),
            )
            self._drain_presses(locator.name, center)
        return '录制已停止'

    def _build_stats(
        self,
        layer: str | None,
        center: tuple[int, int] | None,
        score: float | None,
        angle_ms: float,
        locate_ms: float,
        total_ms: float,
        step_max: float = 0.0,
        window_span: float = 0.0,
    ) -> dict[str, Any]:
        """汇总这一帧的定位统计，供悬浮窗显示。

        Args:
            layer: 命中分层名，未定位到时为 None。
            center: 全局坐标，未定位到时为 None。
            score: 匹配分，未定位到时为 None。
            angle_ms: 朝向识别耗时（毫秒）。
            locate_ms: 定位耗时（毫秒）。
            total_ms: 从截图到出结果的端到端耗时（毫秒）。
            step_max: 统计窗口内单帧最大偏移。
            window_span: 统计窗口的实际时长。

        Returns:
            统计字典，键为 layer/center/score/fps/angle_ms/locate_ms/total_ms/step_max/window_span。
        """
        self._update_fps()
        return {
            'layer': layer,
            'center': center,
            'score': score,
            'fps': self._fps,
            'angle_ms': angle_ms,
            'locate_ms': locate_ms,
            'total_ms': total_ms,
            'step_max': step_max,
            'window_span': window_span,
        }

    def _debounce(self, layer: str, center: tuple[int, int]) -> tuple[int, int]:
        """与上一帧坐标差在容差内就沿用上一帧，避免原地抖动。

        Args:
            layer: 本帧命中的分层名。
            center: 本帧定位出的全局坐标。

        Returns:
            实际采用的坐标。
        """
        previous = self._shown_center
        if (
            previous is not None
            and layer == self._shown_layer
            and abs(previous[0] - center[0]) <= SHAKE_TOLERANCE
            and abs(previous[1] - center[1]) <= SHAKE_TOLERANCE
        ):
            return previous
        self._shown_center = center
        return center

    def _track_step(self, center: tuple[int, int]) -> tuple[float, float]:
        """记录本帧坐标，返回窗口内单帧最大偏移与窗口实际时长。

        Args:
            center: 本帧全局坐标。

        Returns:
            (单帧最大偏移像素, 窗口内最早与最新样本的时间差)。
        """
        now = time.perf_counter()
        self._track.append((now, center))
        while len(self._track) >= 2 and now - self._track[1][0] > SPEED_WINDOW_SECONDS:
            self._track.pop(0)
        step_max = 0.0
        for prev, cur in zip(self._track, self._track[1:], strict=False):
            step = float(np.hypot(cur[1][0] - prev[1][0], cur[1][1] - prev[1][1]))
            if step > step_max:
                step_max = step
        span = self._track[-1][0] - self._track[0][0] if len(self._track) >= 2 else 0.0
        return step_max, span

    def _update_fps(self) -> None:
        """累计帧数并每秒刷新一次帧率。"""
        self._frame_count += 1
        now = time.perf_counter()
        if self._fps_start == 0:
            self._fps_start = now
            return
        elapsed = now - self._fps_start
        if elapsed >= 1.0:
            self._fps = self._frame_count / elapsed
            self._frame_count = 0
            self._fps_start = now

    def _drain_presses(self, layer: str, center: tuple[int, int]) -> None:
        """处理这一轮攒下的按键，按下的顺序依次生效。

        Args:
            layer: 当前命中分层名。
            center: 全局坐标。
        """
        while self._pending:
            key = self._pending.pop(0)
            if key == UNDO_KEY:
                removed = self.route.pop_point()
                if removed is not None:
                    self.on_undo()
                    self.on_status(f'已删上一个点 · 剩 {len(self.route.points)} 点')
                continue
            kind = POINT_KINDS.get(key)
            if kind is None:
                continue
            point = BagelRoutePoint(float(center[0]), float(center[1]), layer, kind[0])
            if self.route.append_point(point):
                self.on_point(point)
                self.on_status(f'已打 {len(self.route.points)} 点 · {kind[1]}')

    @staticmethod
    def _crop(screenshot: MatLike, rect: Rect) -> MatLike | None:
        """裁出小地图并统一成三通道，定位与朝向共用同一份裁剪。

        Args:
            screenshot: 整屏截图。
            rect: 小地图区域。

        Returns:
            小地图裁剪；裁剪失败时返回 None。
        """
        crop = cv2_utils.crop_image_only(screenshot, rect)
        if crop is None or crop.size == 0:
            return None
        if crop.ndim == 2:
            crop = np.stack([crop] * 3, axis=-1)
        return crop

    def _locate(self, crop: MatLike) -> tuple[Any, float, tuple[int, int]] | None:
        """在裁剪出的小地图上定位，失败返回 None。

        Args:
            crop: 小地图裁剪。

        Returns:
            (命中层, 匹配分, 全局坐标)。
        """
        if self._locator is None:
            return None
        result = self._locator.locate(crop)
        if result is None:
            return None
        locator, score, center = result
        return locator, score, center

    def _start_key_listener(self) -> None:
        """只监听打点键与退格键的按下。"""

        def on_press(key: keyboard.Key | keyboard.KeyCode) -> None:
            name = _key_name(key)
            if name is None:
                return
            if name in POINT_KINDS or name == UNDO_KEY:
                self._pending.append(name)

        self._listener = keyboard.Listener(on_press=on_press)
        self._listener.start()

    def _stop_key_listener(self) -> None:
        """关闭按键监听。"""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._pending.clear()

    def _wait_for_foreground(self, hwnd: int) -> bool:
        """等游戏窗口变成前台。

        Args:
            hwnd: 游戏窗口句柄。

        Returns:
            成为前台时为 True。
        """
        deadline = time.monotonic() + FOREGROUND_WAIT_SECONDS
        while time.monotonic() < deadline:
            if is_foreground(hwnd):
                return True
            if self.stop_requested.is_set():
                return False
            time.sleep(0.05)
        return is_foreground(hwnd)


def _key_name(key: keyboard.Key | keyboard.KeyCode) -> str | None:
    """把 pynput 按键统一成小写名字，非字符键返回 None。"""
    char = getattr(key, 'char', None)
    if isinstance(char, str) and char:
        return char.lower()
    name = getattr(key, 'name', None)
    return name.lower() if isinstance(name, str) else None
