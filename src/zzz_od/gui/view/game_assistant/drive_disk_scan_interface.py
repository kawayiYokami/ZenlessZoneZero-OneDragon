from typing import Optional

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from one_dragon.base.operation.application import application_const
from one_dragon_qt.utils.config_utils import get_prop_adapter
from one_dragon_qt.view.app_run_interface import AppRunInterface
from one_dragon_qt.widgets.row import Row
from one_dragon_qt.widgets.setting_card.help_card import HelpCard
from one_dragon_qt.widgets.setting_card.spin_box_setting_card import DoubleSpinBoxSettingCard
from zzz_od.application.drive_disk_scan import drive_disk_scan_const
from zzz_od.application.drive_disk_scan.drive_disk_scan_config import DriveDiskScanConfig
from zzz_od.application.zzz_application import ZApplication
from zzz_od.context.zzz_context import ZContext


class DriveDiskScanInterface(AppRunInterface):

    def __init__(self,
                 ctx: ZContext,
                 parent=None):
        self.ctx: ZContext = ctx
        self.app: Optional[ZApplication] = None

        AppRunInterface.__init__(
            self,
            ctx=ctx,
            app_id=drive_disk_scan_const.APP_ID,
            object_name='drive_disk_scan_interface',
            nav_text_cn='驱动盘自动扫描',
            parent=parent,
        )
        self.config: Optional[DriveDiskScanConfig] = None

    def get_widget_at_top(self) -> QWidget:
        content = Row()
        left_layout = QVBoxLayout()
        content.h_layout.addLayout(left_layout)

        # 使用说明卡片
        self.help_opt = HelpCard(
            url='https://one-dragon.com/zzz/zh/docs/feat_game_assistant.html',
            title='使用说明',
            content='调整语言为英文，打开驱动盘栏，点击开始'
        )
        left_layout.addWidget(self.help_opt)

        return content

    def on_interface_shown(self) -> None:
        AppRunInterface.on_interface_shown(self)
        self.config = self.ctx.run_context.get_config(
            app_id=drive_disk_scan_const.APP_ID,
            instance_idx=self.ctx.current_instance_idx,
            group_id=application_const.DEFAULT_GROUP_ID,
        )

    def on_interface_hidden(self) -> None:
        AppRunInterface.on_interface_hidden(self)