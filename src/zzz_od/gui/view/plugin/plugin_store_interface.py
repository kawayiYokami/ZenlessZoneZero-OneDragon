"""插件商店页。

列出来自插件商店站点的全部插件，未安装的可以订阅（安装），已安装的显示订阅
状态与更新入口。点卡片进入插件详情，可以看说明与更新日志。
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    LineEdit,
    PushButton,
)

from one_dragon.base.operation.application.plugin_market import (
    MarketPlugin,
    MarketSnapshot,
)
from one_dragon.utils.i18_utils import gt
from one_dragon_qt.widgets.plugin_card import (
    BADGE_NONE,
    BADGE_SUBSCRIBED,
    BADGE_UPDATE,
    PluginCard,
    PluginCardGrid,
    PluginPager,
)
from one_dragon_qt.widgets.vertical_scroll_interface import VerticalScrollInterface
from zzz_od.context.zzz_context import ZContext
from zzz_od.gui.view.plugin.plugin_operation_mixin import PluginOperationMixin


class PluginStoreInterface(PluginOperationMixin, VerticalScrollInterface):
    """插件商店页：市场上可订阅的插件。"""

    def __init__(
        self,
        ctx: ZContext,
        parent: QWidget | None = None,
    ) -> None:
        self.init_plugin_operation(ctx)

        VerticalScrollInterface.__init__(
            self,
            object_name='plugin_store_interface',
            content_widget=None,
            parent=parent,
            nav_text_cn='插件商店',
        )
        self.connect_cover_signal()

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def get_fixed_top_widget(self) -> QWidget:
        """顶部固定的搜索与刷新栏。"""
        top = QWidget(self)
        layout = QHBoxLayout(top)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(8)

        self.search_edit = LineEdit(top)
        self.search_edit.setPlaceholderText(gt('搜索插件商店'))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_edit, 1)

        self.refresh_btn = PushButton(FluentIcon.SYNC, gt('刷新'), top)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        layout.addWidget(self.refresh_btn)

        return top

    def get_content_widget(self) -> QWidget:
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.status_label = CaptionLabel(content)
        self.status_label.setTextColor('#8a8a8a', '#9a9a9a')
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        self.card_grid = PluginCardGrid(content)
        layout.addWidget(self.card_grid)

        self.pager = PluginPager(content)
        layout.addWidget(self.pager, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(1)
        self.init_paging(self.card_grid, self.pager)
        return content

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def on_interface_shown(self) -> None:
        VerticalScrollInterface.on_interface_shown(self)
        if self.market_snapshot is None:
            self.load_market()
        else:
            self.refresh_cards()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.close_plugin_operation()
        super().closeEvent(event)

    def on_market_loading(self) -> None:
        self._set_status(gt('正在读取插件商店…'))

    def on_market_loaded(self, snapshot: MarketSnapshot) -> None:
        if not self._listed_entries():
            self._set_status(gt('插件商店读取失败，请检查网络后点击刷新'))

    # ------------------------------------------------------------------
    # 卡片
    # ------------------------------------------------------------------

    def _listed_entries(self) -> list[MarketPlugin]:
        snapshot = self.market_snapshot
        if snapshot is None:
            return []
        return [entry for entry in snapshot.plugins if entry.is_listed]

    def refresh_cards(self) -> None:
        """重建商店卡片。"""
        self.card_grid.clear()
        self.reset_card_registry()

        entries = self._listed_entries()
        installed_map = self.installed_market_map()
        cards: list[PluginCard] = []
        for entry in entries:
            plugin_info = installed_map.get(entry.plugin_id)
            update_version = (
                self.pending_update_version(plugin_info, entry)
                if plugin_info is not None else ''
            )
            if plugin_info is None:
                badge_text = gt('未订阅')
                badge_kind = BADGE_NONE
            elif update_version:
                badge_text = gt('可更新 v{version}').format(version=update_version)
                badge_kind = BADGE_UPDATE
            else:
                badge_text = gt('已订阅')
                badge_kind = BADGE_SUBSCRIBED

            meta_parts = []
            if entry.latest_version:
                meta_parts.append(f'v{entry.latest_version}')
            if entry.author_name:
                meta_parts.append(entry.author_name)

            card = self.make_card(
                title=entry.name,
                meta_text=' · '.join(meta_parts),
                seed=entry.plugin_id,
                cover=self.cover_for(entry.plugin_id, entry.cover_url, plugin_info),
                badge_text=badge_text,
                badge_kind=badge_kind,
                on_click=partial(self.open_market_detail, entry),
                parent=self.card_grid,
            )
            self.remember_card(card, entry.plugin_id, entry.cover_url)
            self.card_grid.add_card(card)
            cards.append(card)

        if entries:
            snapshot = self.market_snapshot
            text = gt('商店共 {count} 个插件').format(count=len(entries))
            if snapshot is not None and snapshot.from_cache:
                text += gt('（来自缓存）')
            self._set_status(text)
        self.set_cards(cards)

    def set_controls_enabled(self, enabled: bool) -> None:
        self.refresh_btn.setEnabled(enabled)
        for card in self._all_cards:
            card.set_enabled_state(enabled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update_paging()

    def _on_search_changed(self, text: str) -> None:
        self.set_card_filter(text)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _on_refresh_clicked(self) -> None:
        if self._import_busy:
            return
        self.load_market(force=True)
