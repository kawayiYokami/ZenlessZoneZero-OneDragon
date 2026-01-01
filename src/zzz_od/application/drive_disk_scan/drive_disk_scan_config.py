from one_dragon.base.operation.application.application_config import ApplicationConfig
from zzz_od.application.drive_disk_scan import drive_disk_scan_const


class DriveDiskScanConfig(ApplicationConfig):

    def __init__(self, instance_idx: int, group_id: str):
        ApplicationConfig.__init__(
            self,
            app_id=drive_disk_scan_const.APP_ID,
            instance_idx=instance_idx,
            group_id=group_id,
        )

    @property
    def click_interval(self) -> float:
        """点击间隔时间（秒）"""
        return max(0.1, self.get('click_interval', 0.1))

    @click_interval.setter
    def click_interval(self, new_value: float) -> None:
        self.update('click_interval', new_value)