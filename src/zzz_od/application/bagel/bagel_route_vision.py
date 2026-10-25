"""固定底图定位、出生识别与独立的角色、镜头方向。"""

from __future__ import annotations

from math import hypot
from typing import TYPE_CHECKING

from zzz_od.application.bagel.bagel_fixed_map import load_fixed_map
from zzz_od.application.bagel.bagel_flow import load_published_flow
from zzz_od.application.bagel.bagel_heading import character_heading
from zzz_od.application.bagel.bagel_map_locator import locate_on_map
from zzz_od.application.bagel.bagel_observation import BagelObserver
from zzz_od.application.bagel.bagel_route import SUPPORTED_MAP_IDS, BagelRoute

if TYPE_CHECKING:
    from collections.abc import Mapping

    import numpy as np

    from zzz_od.application.bagel.bagel_fixed_map import BagelFixedMap
    from zzz_od.application.bagel.bagel_heading import HeadingObservation
    from zzz_od.application.bagel.bagel_map_locator import MapLocation


class BagelRouteVision:
    """出生识别、正式导航和开发试跑共用一份固定地图快照。"""

    def __init__(
        self,
        map_id: str = 'janus_high_a',
        route_data: BagelRoute | None = None,
        map_snapshot: BagelFixedMap | None = None,
    ) -> None:
        """独立加载或接收本次执行的地图，正式路点仅来自发布流程。"""
        self.map_id: str = map_id
        self.map: BagelFixedMap = map_snapshot if map_snapshot is not None else load_fixed_map(map_id)
        if self.map.map_id != map_id:
            raise ValueError('导航路线与固定地图不一致')
        if route_data is None:
            points = dict.fromkeys(point for step in load_published_flow(map_id).steps for point in step.waypoints)
            self.route: BagelRoute = BagelRoute(map_id, tuple(points))
        else:
            self.route = BagelRoute.from_dict(map_id, route_data.to_dict(), complete=False, check_roles=False)
        self.anchor: tuple[float, float] = self.map.anchor
        self.spawn: tuple[float, float] = self.map.spawn
        self.arrow_settings: Mapping[str, object] = self.map.arrow_settings
        self.last_location: MapLocation | None = None
        self._camera_observer: BagelObserver | None = None

    def locate(self, crop: np.ndarray) -> tuple[float, float] | None:
        """返回原路线坐标或明确失败，并保存本帧诊断。"""
        self.last_location = locate_on_map(self.map, crop)
        return self.last_location.position

    def player_angle(self, crop: np.ndarray) -> float | None:
        """读取黄色箭头亮色尖端的方向；图像右方为零、顺时针为正。"""
        return character_heading(crop, self.anchor, self.arrow_settings).angle

    def camera_heading(self, crop: np.ndarray) -> HeadingObservation:
        """读取本帧镜头方向及拒绝原因；不使用角色箭头代替镜头。"""
        if self._camera_observer is None:
            self._camera_observer = BagelObserver()
        return self._camera_observer.camera_heading(crop)

    def is_at_spawn(self, crop: np.ndarray) -> bool:
        """位置靠近本路线出生点才识别为该路线。"""
        position = self.locate(crop)
        return position is not None and hypot(position[0] - self.spawn[0], position[1] - self.spawn[1]) <= 5


class BagelSpawnMatcher:
    """在已支持的出生路线中选一条，未知则返回 None。"""

    def __init__(self, routes: dict[str, BagelRoute] | None = None) -> None:
        """预加载全部支持路线，避免每局反复读盘。"""
        self.routes: dict[str, BagelRouteVision] = {
            map_id: BagelRouteVision(map_id, (routes or {}).get(map_id)) for map_id in SUPPORTED_MAP_IDS
        }

    def match(self, crop: np.ndarray) -> str | None:
        """返回命中的 map_id；多条同时命中则拒绝。"""
        matches = [map_id for map_id in SUPPORTED_MAP_IDS if self.routes[map_id].is_at_spawn(crop)]
        return matches[0] if len(matches) == 1 else None

    def vision(self, map_id: str) -> BagelRouteVision:
        """取已加载的路线视觉。"""
        if map_id not in self.routes:
            raise ValueError(f'不支持的贝果路线: {map_id}')
        return self.routes[map_id]
