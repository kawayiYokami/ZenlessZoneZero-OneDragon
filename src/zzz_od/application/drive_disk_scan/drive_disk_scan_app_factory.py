from __future__ import annotations

from typing import TYPE_CHECKING

from one_dragon.base.operation.application.application_config import ApplicationConfig
from one_dragon.base.operation.application.application_factory import ApplicationFactory
from one_dragon.base.operation.application_base import Application
from zzz_od.application.drive_disk_scan import drive_disk_scan_const
from zzz_od.application.drive_disk_scan.drive_disk_scan_app import DriveDiskScanApp
from zzz_od.application.drive_disk_scan.drive_disk_scan_config import DriveDiskScanConfig

if TYPE_CHECKING:
    from zzz_od.context.zzz_context import ZContext


class DriveDiskScanAppFactory(ApplicationFactory):

    def __init__(self, ctx: ZContext):
        ApplicationFactory.__init__(
            self,
            app_id=drive_disk_scan_const.APP_ID,
            app_name=drive_disk_scan_const.APP_NAME,
        )
        self.ctx: ZContext = ctx

    def create_application(self, instance_idx: int, group_id: str) -> Application:
        return DriveDiskScanApp(self.ctx)

    def create_config(
        self, instance_idx: int, group_id: str
    ) -> ApplicationConfig:
        return DriveDiskScanConfig(
            instance_idx=instance_idx,
            group_id=group_id,
        )