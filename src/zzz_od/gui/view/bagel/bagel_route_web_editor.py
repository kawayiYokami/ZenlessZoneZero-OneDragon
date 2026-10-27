"""贝果路线编辑器：顶部是 Python 按钮栏，下面是网页。

网页负责显示底图、全部路线与选中路线的点位；Python 负责录制与写盘。
两边只走单向推送：Python 把路线数据与录制中的实时状态推给网页，
网页不反向调用 Python，避免通道两端的状态互相踩。

页面用 ``setHtml`` 加载并把 baseUrl 指向工作目录，这样页面里的相对路径
能直接读到 ``assets/game_data/map_locator`` 下的底图与清单。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRect, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    PrimaryPushButton,
    PushButton,
    Theme,
)

from one_dragon.utils import os_utils
from one_dragon.utils.log_utils import log
from zzz_od.application.bagel.bagel_route_recorder import (
    ELEVATOR,
    ELEVATOR_EXIT_BACKWARD,
    ELEVATOR_EXIT_FORWARD,
    KIND_COLORS,
    KIND_LABELS,
    POINT_KINDS,
    BagelPointRecorder,
    BagelRoute,
    BagelRoutePoint,
    delete_route,
    is_foreground,
    list_routes,
    rename_route,
    route_dir,
    set_route_enabled,
)
from zzz_od.gui.view.bagel.bagel_light_theme import (
    apply_light_palette,
    enter_light_theme,
    restore_theme,
)
from zzz_od.gui.view.bagel.bagel_record_overlay import BagelRecordOverlay
from zzz_od.gui.view.bagel.bagel_run_overlay import BagelRunOverlay

if TYPE_CHECKING:
    from zzz_od.context.zzz_context import ZContext

WEB_DIR = Path(__file__).parent / 'web'
"""网页资源目录。"""

OVERLAY_INTERVAL_MS = 150
"""悬浮窗跟随游戏窗口位置与显隐的刷新间隔。"""

UNDO_KEY_LABEL = '退格'
"""删除键在界面上的显示名。"""


class _KeyCap(QLabel):
    """单个按键的键帽样式标签，形状按内容宽度自适应。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        """
        Args:
            text: 键名，如 1、退格。
            parent: 父窗口。
        """
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(24)
        self.setStyleSheet(
            'QLabel {'
            ' color: #3b3b3b;'
            ' background: #ffffff;'
            ' border: 1px solid #c8ccd2;'
            ' border-bottom: 2px solid #a8adb5;'
            ' border-radius: 4px;'
            ' padding: 0 7px;'
            ' font-size: 13px;'
            '}'
        )


class _KeyName(QLabel):
    """键帽右侧的类型名，用普通正文色。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        """
        Args:
            text: 类型名，如 途径点。
            parent: 父窗口。
        """
        super().__init__(text, parent)
        self.setStyleSheet('QLabel { color: #4a4a4a; font-size: 12px; }')


class _KindDot(QLabel):
    """类型名前面的圆点，颜色与网页画该类型点的颜色一致。"""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        """
        Args:
            color: 圆点颜色，取自 KIND_COLORS。
            parent: 父窗口。
        """
        super().__init__('', parent)
        self.setFixedSize(8, 8)
        self.setStyleSheet(
            f'QLabel {{ background: {color}; border-radius: 4px; }}'
        )


class BagelEditorBridge(QObject):
    """网页调用 Python 的入口：删除路线、试跑路线、保存编辑后的点位。

    网页通过 QWebChannel 调用，删除由 Python 删文件后重新推列表，
    试跑由 Python 起后台线程按打点顺序走一遍，网页只负责显示状态，
    编辑后的点位由 Python 校验并写回同一个路线文件。
    """

    deleted = Signal(str, bool)
    """(路线标识, 是否真的删掉)。"""

    test_requested = Signal(str)
    """(要试跑的路线标识)。"""

    save_requested = Signal(str, str)
    """(路线标识, 点位 JSON)，由界面线程写盘。"""

    renamed = Signal(str, str)
    """(原标识, 新标识)，失败时新标识为空串。"""

    toggled = Signal(str, bool, bool)
    """(路线标识, 是否启用, 是否真的改了)。"""

    locate_requested = Signal(int)
    """(要替换坐标的点序号)，网页点了某一行的定位按钮。"""

    @Slot(str)
    def deleteRoute(self, route_id: str) -> None:
        """删除一条路线并广播结果。

        Args:
            route_id: 路线标识。
        """
        ok = delete_route(route_id)
        log.info('贝果路线删除 %s：%s', route_id, '成功' if ok else '文件不存在')
        self.deleted.emit(route_id, ok)

    @Slot(str, str)
    def renameRoute(self, route_id: str, new_id: str) -> None:
        """给一条路线改名。

        Args:
            route_id: 原路线标识。
            new_id: 新路线标识。
        """
        try:
            actual = rename_route(route_id, new_id)
        except (OSError, ValueError) as error:
            log.warning('贝果路线改名失败 %s -> %s：%s', route_id, new_id, error)
            self.renamed.emit(route_id, '')
            return
        log.info('贝果路线改名 %s -> %s', route_id, actual)
        self.renamed.emit(route_id, actual)

    @Slot(str, bool)
    def setRouteEnabled(self, route_id: str, enabled: bool) -> None:
        """开关一条路线，关闭后执行时不生效。

        Args:
            route_id: 路线标识。
            enabled: True 启用、False 关闭。
        """
        try:
            changed = set_route_enabled(route_id, enabled)
        except (OSError, ValueError, KeyError) as error:
            log.warning('贝果路线开关失败 %s -> %s：%s', route_id, enabled, error)
            self.toggled.emit(route_id, not enabled, False)
            return
        log.info('贝果路线 %s 已%s', route_id, '启用' if enabled else '关闭')
        self.toggled.emit(route_id, enabled, changed)

    @Slot(int)
    def locatePoint(self, index: int) -> None:
        """请求切到游戏定位一次，用结果替换第 index 个点的坐标。

        Args:
            index: 路线内点的下标，从 0 开始。
        """
        self.locate_requested.emit(index)

    @Slot(str)
    def testRoute(self, route_id: str) -> None:
        """请求试跑一条路线。

        Args:
            route_id: 路线标识。
        """
        self.test_requested.emit(route_id)

    @Slot(str, str)
    def saveRoute(self, route_id: str, points_json: str) -> None:
        """网页编辑后请求保存。

        只把原始数据转给界面线程，写盘与校验都在界面线程里做，
        避免在网页通道的调用栈里做文件操作。

        Args:
            route_id: 路线标识。
            points_json: 编辑后的点位数组 JSON。
        """
        self.save_requested.emit(route_id, points_json)


class BagelRouteEditor(QDialog):
    """路线编辑器；录制与写盘在 Python，显示在网页。"""

    def __init__(self, ctx: ZContext, parent: QWidget | None = None) -> None:
        """
        Args:
            ctx: 已初始化的上下文，录制复用其截图与定位服务。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.ctx: ZContext = ctx
        self.recorder: BagelPointRecorder | None = None
        self.thread: Any = None
        self.test_thread: Any = None
        self.locate_thread: Any = None
        self._locate_index: int = -1
        """正在定位替换的点序号；-1 表示没有进行中的定位。"""
        self.run_overlay: BagelRunOverlay | None = None
        self._recording_route: BagelRoute | None = None
        self._page_ready: bool = False
        self._shown_layer: str | None = None
        self._layer_origins: dict[str, tuple[int, int]] = _layer_origins()
        self.overlay: BagelRecordOverlay | None = None
        self._overlay_timer: QTimer = QTimer(self)
        self._overlay_timer.setInterval(OVERLAY_INTERVAL_MS)
        self._overlay_timer.timeout.connect(self._sync_overlay)
        self._run_overlay_timer: QTimer = QTimer(self)
        self._run_overlay_timer.setInterval(OVERLAY_INTERVAL_MS)
        self._run_overlay_timer.timeout.connect(self._sync_run_overlay)

        self._previous_theme: Theme = enter_light_theme()
        apply_light_palette(self)
        self.setWindowTitle('贝果路线编辑器')
        self.resize(1280, 800)
        self._build_ui()

    def _build_key_hint(self, bar: QWidget) -> QWidget:
        """把打点键位做成键帽加名字的一行，供顶部栏右侧显示。

        Args:
            bar: 顶部按钮栏，作为控件的父窗口。

        Returns:
            装好全部键位说明的容器。
        """
        holder = QWidget(bar)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        # 颜色圆点跟在键帽后面、名字前面，和网页画点用的是同一套颜色。
        items = [(key, kind, label) for key, (kind, label) in POINT_KINDS.items()]
        for index, (key, kind, label) in enumerate(items):
            if index > 0:
                layout.addSpacing(8)
            layout.addWidget(_KeyCap(key, holder))
            layout.addSpacing(2)
            layout.addWidget(
                _KindDot(KIND_COLORS[kind], holder), 0,
                Qt.AlignmentFlag.AlignVCenter,
            )
            layout.addWidget(_KeyName(label, holder))
        layout.addSpacing(8)
        layout.addWidget(_KeyCap(UNDO_KEY_LABEL, holder))
        layout.addWidget(_KeyName('删上一个点', holder))
        return holder

    def _build_ui(self) -> None:
        """顶部按钮栏加网页视图。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget(self)
        bar.setFixedHeight(52)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 8, 16, 8)
        bar_layout.setSpacing(8)

        self.start_button: PrimaryPushButton = PrimaryPushButton('录制路线', bar)
        self.start_button.clicked.connect(self._start_recording)
        bar_layout.addWidget(self.start_button)
        self.stop_button: PushButton = PushButton('停止录制', bar)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_recording)
        bar_layout.addWidget(self.stop_button)
        bar_layout.addSpacing(12)
        self.status_label: BodyLabel = BodyLabel('就绪', bar)
        bar_layout.addWidget(self.status_label)
        bar_layout.addStretch(1)
        bar_layout.addWidget(self._build_key_hint(bar))
        layout.addWidget(bar)

        self.view: QWebEngineView = QWebEngineView(self)
        settings = self.view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False,
        )
        self.view.loadFinished.connect(self._on_page_loaded)
        layout.addWidget(self.view, 1)

        self.bridge: BagelEditorBridge = BagelEditorBridge(self)
        self.bridge.deleted.connect(self._on_route_deleted)
        self.bridge.test_requested.connect(self._start_test)
        self.bridge.save_requested.connect(self._on_save_requested)
        self.bridge.renamed.connect(self._on_route_renamed)
        self.bridge.toggled.connect(self._on_route_toggled)
        self.bridge.locate_requested.connect(self._start_locate)
        self.channel: QWebChannel = QWebChannel(self.view.page())
        self.channel.registerObject('bagelBridge', self.bridge)
        self.view.page().setWebChannel(self.channel)

        self._load_page()

    def _load_page(self) -> None:
        """把页面载入并指定 baseUrl，让页面能读到工作目录下的底图。"""
        html = (WEB_DIR / 'index.html').read_text(encoding='utf-8')
        base = QUrl.fromLocalFile(str(Path(os_utils.get_work_dir())) + '/')
        self.view.setHtml(html, base)

    def _on_page_loaded(self, ok: bool) -> None:
        """页面载入完成后推一次路线列表。"""
        if not ok:
            self.status_label.setText('页面载入失败')
            return
        self._page_ready = True
        self._refresh_routes()

    def _run_js(self, script: str) -> None:
        """页面没准备好时直接丢弃，避免刷一堆控制台报错。"""
        if self._page_ready:
            self.view.page().runJavaScript(script)

    def _refresh_routes(self) -> None:
        """把磁盘上的全部路线推给网页。"""
        try:
            routes = list_routes()
        except OSError as error:
            log.error('读取贝果路线失败', exc_info=True)
            self.status_label.setText(f'读取路线失败：{error}')
            return
        payload = json.dumps([_route_json(route) for route in routes], ensure_ascii=False)
        self._run_js(f'window.bagelEditor.setRoutes({payload});')

    def _on_route_deleted(self, route_id: str, ok: bool) -> None:
        """网页请求删除后刷新列表并提示结果。

        Args:
            route_id: 被删除的路线标识。
            ok: 是否真的删掉了文件。
        """
        self.status_label.setText(
            f'已删除 {route_id}' if ok else f'路线文件不存在：{route_id}',
        )
        self._refresh_routes()

    def _on_route_renamed(self, old_id: str, new_id: str) -> None:
        """改名后刷新列表，并让网页把选中项切到新名字。

        Args:
            old_id: 原路线标识。
            new_id: 新路线标识；空串表示改名失败。
        """
        if not new_id:
            self.status_label.setText(f'改名失败：{old_id}')
            self._refresh_routes()
            return
        self.status_label.setText(f'已改名：{old_id} → {new_id}')
        self._refresh_routes()

    def _on_route_toggled(self, route_id: str, enabled: bool, changed: bool) -> None:
        """路线开关写盘后刷新列表。

        Args:
            route_id: 路线标识。
            enabled: 网页请求的目标状态。
            changed: 是否真的改动了文件。
        """
        if not changed:
            self.status_label.setText(f'路线开关未改动：{route_id}')
        else:
            self.status_label.setText(
                f'{route_id} 已{"启用" if enabled else "关闭"}'
                + ('' if enabled else '，执行时跳过这条路线'),
            )
        self._refresh_routes()

    def _on_save_requested(self, route_id: str, points_json: str) -> None:
        """网页编辑后写回路线文件。

        只覆盖点位，路线标识与时间戳沿用原文件；写盘前校验每个点
        的坐标与类型，任何一条不合法就整体放弃，避免把路线写坏。

        Args:
            route_id: 路线标识。
            points_json: 编辑后的点位数组 JSON。
        """
        path = route_dir() / f'{route_id}.route.json'
        if not path.is_file():
            self.status_label.setText(f'路线文件不存在，无法保存：{route_id}')
            return
        try:
            raw = json.loads(points_json)
            if not isinstance(raw, list):
                raise ValueError('点位不是数组')
            points = [_parse_point(item) for item in raw]
            route = BagelRoute.load(path)
            route.points = points
            route.save(path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            log.error('保存贝果路线 %s 失败：%s', route_id, error)
            self.status_label.setText(f'保存失败：{error}')
            return
        self.status_label.setText(f'已保存 {route_id} · {len(points)} 点')
        log.info('贝果路线 %s 编辑后已保存 %s 点', route_id, len(points))

    def _start_locate(self, index: int) -> None:
        """切到游戏定位一次，把结果写到第 index 个点。

        Args:
            index: 要替换坐标的点序号，从 0 开始。
        """
        if self.locate_thread is not None:
            self.status_label.setText('定位还在进行')
            return
        if self.recorder is not None:
            self.status_label.setText('正在录制，先停止录制再定位')
            return
        from zzz_od.gui.view.bagel.bagel_locate_thread import BagelLocateThread

        thread = BagelLocateThread(self.ctx, self)
        self.locate_thread = thread
        self._locate_index = index
        thread.status_changed.connect(self.status_label.setText)
        thread.done.connect(self._on_locate_done)
        thread.finished.connect(self._clear_locate_thread)
        self.status_label.setText(f'正在切到游戏定位第 {index + 1} 点…')
        thread.start()

    def _clear_locate_thread(self) -> None:
        """定位线程结束后放开引用。"""
        self.locate_thread = None

    def _on_locate_done(
        self, ok: bool, message: str, layer_name: str, global_xy: object,
    ) -> None:
        """把定位结果写回选中的点，并刷新网页上的坐标。

        Args:
            ok: 是否定位成功。
            message: 结论文案。
            layer_name: 命中的分层名。
            global_xy: 全局坐标；失败时为 None。
        """
        index = self._locate_index
        self._locate_index = -1
        if not ok or global_xy is None:
            self.status_label.setText(message)
            return
        self.status_label.setText(message)
        coords = json.dumps([round(global_xy[0]), round(global_xy[1])])
        name = json.dumps(layer_name, ensure_ascii=False)
        self._run_js(f'window.bagelEditor.setPointPosition({index}, {name}, {coords});')

    def _start_test(self, route_id: str) -> None:
        """在游戏里试跑一条路线：站在起点附近，按打点顺序走一遍。

        Args:
            route_id: 要试跑的路线标识。
        """
        if self.test_thread is not None:
            self.status_label.setText('已有试跑在进行')
            return
        if self.recorder is not None:
            self.status_label.setText('正在录制，先停止录制再试跑')
            return
        from zzz_od.gui.view.bagel.bagel_route_test_thread import BagelRouteTestThread

        thread = BagelRouteTestThread(self.ctx, route_id, self)
        self.test_thread = thread
        thread.status_changed.connect(self._on_test_status)
        thread.overlay_state.connect(self._on_run_state)
        thread.done.connect(self._on_test_done)
        thread.finished.connect(self._clear_test_thread)
        self.status_label.setText(f'正在切到游戏，准备试跑 {route_id}')
        self.start_button.setEnabled(False)
        thread.start()

    def _on_run_state(self, snapshot: object) -> None:
        """试跑状态同步到执行态悬浮窗；None 表示收起。

        Args:
            snapshot: 执行器状态字典，或 None。
        """
        if snapshot is None:
            if self.run_overlay is not None:
                self.run_overlay.hide()
            return
        if not isinstance(snapshot, dict):
            return
        if self.run_overlay is None:
            self.run_overlay = BagelRunOverlay()
            self._run_overlay_timer.start()
        from zzz_od.gui.view.bagel.bagel_route_test_thread import decision_lines

        self.run_overlay.set_lines(decision_lines(snapshot))
        layer = str(snapshot.get('layer') or '')
        layer_image = snapshot.get('layer_image')
        if layer and isinstance(layer_image, Path):
            self.run_overlay.set_layer(layer, layer_image)
        position = snapshot.get('position')
        if isinstance(position, tuple) and len(position) == 2:
            self.run_overlay.set_position(position)
        else:
            self.run_overlay.set_position(None)
        self.run_overlay.set_route(
            list(snapshot.get('points') or []), int(snapshot.get('cursor') or 0),
        )
        self.run_overlay.show()

    def _sync_run_overlay(self) -> None:
        """执行态悬浮窗跟随游戏窗口位置与显隐。"""
        overlay = self.run_overlay
        if overlay is None:
            return
        overlay.set_game_rect(_game_window_rect(self.ctx))

    def _on_test_status(self, text: str) -> None:
        """试跑阶段文案同步到状态栏。"""
        self.status_label.setText(text)

    def _on_test_done(self, success: bool, message: str) -> None:
        """试跑结束显示结论，并恢复按钮。"""
        self.status_label.setText(message)
        self.start_button.setEnabled(True)
        log.info('贝果路线试跑结束：%s', message)

    def _clear_test_thread(self) -> None:
        """线程退出后清引用，允许再次试跑。"""
        if self.test_thread is not None:
            self.test_thread.wait()
        self.test_thread = None
        self.start_button.setEnabled(True)

    def _start_recording(self) -> None:
        """开始录制；路线标识按时间戳自动生成。"""
        if self.recorder is not None:
            return
        route_id = f'route_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        recorder = BagelPointRecorder(
            self.ctx,
            route_id,
            on_point=self._on_point,
            on_undo=self._on_undo,
            on_status=self._on_status,
            on_layer=self._on_layer,
            on_position=self._on_position,
        )
        self.recorder = recorder
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText('正在准备…')
        self._run_js(f'window.bagelEditor.startRecording({json.dumps(route_id)});')
        self.overlay = BagelRecordOverlay()
        self.overlay.set_lines(['定位中...'])
        self.overlay.set_recording(True)
        self._overlay_timer.start()

        from zzz_od.gui.view.bagel.bagel_record_thread import BagelRecordThread

        self.thread = BagelRecordThread(recorder, self)
        self.thread.point_ready.connect(self._on_point)
        self.thread.undo_happened.connect(self._on_undo)
        self.thread.status_changed.connect(self._on_status)
        self.thread.layer_changed.connect(self._on_layer)
        self.thread.position_changed.connect(self._on_position)
        self.thread.angles_changed.connect(self._on_angles)
        self.thread.stats_changed.connect(self._on_stats)
        self.thread.done.connect(self._on_recording_done)
        self.thread.start()

    def _stop_recording(self) -> None:
        """请求停止，保存由录制线程收尾。"""
        if self.recorder is None:
            return
        self.stop_button.setEnabled(False)
        self.status_label.setText('正在停止并保存…')
        self.recorder.request_stop()

    def _on_point(self, point: BagelRoutePoint) -> None:
        """把一个点推给网页，同时画到悬浮窗上。"""
        self._run_js(
            'window.bagelEditor.addPoint('
            + json.dumps(_point_json(point), ensure_ascii=False)
            + ');'
        )
        if self.overlay is not None:
            layer = self._layer_origins.get(point.layer)
            if layer is not None:
                self.overlay.add_point(
                    (point.x - layer[0], point.y - layer[1]), point.kind,
                )

    def _on_undo(self) -> None:
        """删掉上一个点后让网页同步移除，悬浮窗按剩余点重画。"""
        self._run_js('window.bagelEditor.popPoint();')
        if self.overlay is not None and self.recorder is not None:
            self.overlay.clear_points()
            for point in self.recorder.route.points:
                layer = self._layer_origins.get(point.layer)
                if layer is not None:
                    self.overlay.add_point(
                        (point.x - layer[0], point.y - layer[1]), point.kind,
                    )

    def _on_status(self, text: str) -> None:
        """线程报告的阶段变化。"""
        self.status_label.setText(text)

    def _on_layer(self, layer_name: str, image_path: Path) -> None:
        """命中层变化：记下层名、让悬浮窗换底图。"""
        self._shown_layer = layer_name
        if self.overlay is not None:
            self.overlay.set_layer(layer_name, image_path)
            self.overlay.clear_points()
            if self.recorder is not None:
                for point in self.recorder.route.points:
                    origin = self._layer_origins.get(point.layer)
                    if origin is not None and point.layer == layer_name:
                        self.overlay.add_point(
                            (point.x - origin[0], point.y - origin[1]), point.kind,
                        )
        log.info('贝果路线录制命中分层 %s（%s）', layer_name, image_path.name)

    def _on_position(self, local_xy: tuple[float, float] | None) -> None:
        """把当前位置推给网页与悬浮窗；层名用最近一次命中层。"""
        if self.overlay is not None:
            self.overlay.set_position(local_xy)
        if local_xy is None:
            self._run_js('window.bagelEditor.setPosition(null, null);')
            return
        layer = json.dumps(self._shown_layer, ensure_ascii=False)
        coords = json.dumps([round(local_xy[0], 1), round(local_xy[1], 1)])
        self._run_js(f'window.bagelEditor.setPosition({layer}, {coords});')

    def _on_angles(self, sector: float | None, marker: float | None) -> None:
        """把朝向推给悬浮窗的罗盘。"""
        if self.overlay is not None:
            self.overlay.set_angles(sector, marker)

    def _on_stats(self, stats: dict[str, Any]) -> None:
        """按插件原样拼出悬浮窗的定位详情行。

        Args:
            stats: 录制器每帧汇总的统计，字段见 ``BagelPointRecorder._build_stats``。
        """
        if self.overlay is None:
            return
        center = stats.get('center')
        score = stats.get('score')
        layer = stats.get('layer')
        if center is None or score is None or layer is None:
            self.overlay.set_lines([
                '未找到匹配地图',
                f'定位 {stats.get("locate_ms", 0.0):.1f}ms',
            ])
            return
        sector = self.overlay.sector_angle
        marker = self.overlay.marker_angle
        self.overlay.set_lines([
            f'{layer}  ({center[0]}, {center[1]})',
            f'扇形 {_fmt_angle(sector)}  菱形 {_fmt_angle(marker)}',
            f'匹配分 {score:.3f}  {stats.get("fps", 0.0):.0f} FPS',
            f'单帧最大偏移 {stats.get("step_max", 0.0):.0f}px'
            f'/{stats.get("window_span", 0.0):.2f}s',
            f'延迟 {stats.get("total_ms", 0.0):.0f}ms'
            f'  定位 {stats.get("locate_ms", 0.0):.1f}ms'
            f'  朝向 {stats.get("angle_ms", 0.0):.1f}ms',
        ])

    def _sync_overlay(self) -> None:
        """把悬浮窗贴到游戏窗口上；游戏不在前台就隐藏。"""
        if self.overlay is None:
            return
        self.overlay.set_game_rect(_game_window_rect(self.ctx))

    def _on_recording_done(self, success: bool, message: str) -> None:
        """收尾：撤掉悬浮窗、恢复按钮、刷新路线列表。"""
        self._overlay_timer.stop()
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None
        self.status_label.setText(message)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if self.thread is not None:
            self.thread.wait(3000)
        self.thread = None
        self.recorder = None
        self._run_js('window.bagelEditor.stopRecording();')
        if success:
            self._refresh_routes()
        else:
            log.info('贝果路线录制未成功：%s', message)

    def closeEvent(self, event: Any) -> None:
        """关窗时先停录制。"""
        if self.recorder is not None:
            self.recorder.request_stop()
            if self.thread is not None:
                self.thread.wait(5000)
        restore_theme(self._previous_theme)
        super().closeEvent(event)


def _fmt_angle(value: float | None) -> str:
    """朝向角度的显示格式，识别不出时显示 --。

    Args:
        value: 角度值。

    Returns:
        带度数符号的文本。
    """
    return '--' if value is None else f'{value:.0f}°'


def _layer_origins() -> dict[str, tuple[int, int]]:
    """读出各层的画布偏移，用于把全局坐标换算成层内坐标。

    Returns:
        分层名到 origin 的映射；读取失败时返回空映射。
    """
    try:
        from zzz_od.game_data.map_locator.map_locator import load_map_locators

        return {
            locator.name: locator.origin for locator in load_map_locators()
        }
    except (OSError, ValueError) as error:
        log.warning('读取分层偏移失败，悬浮窗不画点：%s', error)
        return {}


def _game_window_rect(ctx: ZContext) -> QRect | None:
    """取游戏窗口矩形；窗口不在前台时返回 None，让悬浮窗隐藏。

    Args:
        ctx: 运行上下文。

    Returns:
        游戏窗口在屏幕上的矩形，不可用时为 None。
    """
    controller = ctx.controller
    if controller is None:
        return None
    game_win = controller.game_win
    hwnd = game_win.get_hwnd()
    if hwnd is None or not is_foreground(hwnd):
        return None
    rect = game_win.win_rect
    if rect is None:
        return None
    return QRect(rect.x1, rect.y1, rect.width, rect.height)


def _point_json(point: BagelRoutePoint) -> dict[str, Any]:
    """点的网页表示。"""
    data: dict[str, Any] = {
        'x': point.x,
        'y': point.y,
        'layer': point.layer,
        'kind': point.kind,
    }
    if point.kind == ELEVATOR:
        data['elevator_option'] = point.elevator_option
        data['elevator_exit'] = point.elevator_exit
    return data


COORDINATE_LIMIT = 4000.0
"""编辑落点的坐标上限，防止网页传来离谱数值把路线写坏。"""


def _parse_point(item: Any) -> BagelRoutePoint:
    """校验网页传来的一条点位。

    Args:
        item: 网页点位对象。

    Returns:
        校验后的点。

    Raises:
        ValueError: 字段缺失、类型不支持或坐标越界。
    """
    if not isinstance(item, dict):
        raise ValueError('点位不是对象')
    kind = str(item['kind'])
    if kind not in KIND_LABELS:
        raise ValueError(f'不支持的点的类型：{kind}')
    layer = str(item['layer'])
    if not layer:
        raise ValueError('点位缺少分层')
    x = float(item['x'])
    y = float(item['y'])
    if not (-COORDINATE_LIMIT <= x <= COORDINATE_LIMIT):
        raise ValueError(f'x 越界：{x}')
    if not (-COORDINATE_LIMIT <= y <= COORDINATE_LIMIT):
        raise ValueError(f'y 越界：{y}')
    option = str(item.get('elevator_option', '')).strip()
    exit_direction = str(item.get('elevator_exit', ELEVATOR_EXIT_FORWARD))
    if exit_direction not in (ELEVATOR_EXIT_FORWARD, ELEVATOR_EXIT_BACKWARD):
        raise ValueError(f'电梯出梯方向不支持：{exit_direction}')
    if kind != ELEVATOR:
        option = ''
        exit_direction = ELEVATOR_EXIT_FORWARD
    return BagelRoutePoint(
        round(x), round(y), layer, kind,
        elevator_option=option, elevator_exit=exit_direction,
    )


def _route_json(route: BagelRoute) -> dict[str, Any]:
    """整条路线的网页表示；点的顺序就是路线，不需要额外连线。"""
    return {
        'route_id': route.route_id,
        'started_at': route.started_at,
        'enabled': route.enabled,
        'points': [_point_json(point) for point in route.points],
        'kinds': KIND_LABELS,
    }
