import os
from functools import cached_property

from cv2.typing import MatLike

from one_dragon.base.config.yaml_operator import YamlOperator
from one_dragon.base.geometry.point import Point
from one_dragon.utils import os_utils, cv2_utils
from one_dragon.utils.log_utils import log
from zzz_od.context.zzz_context import ZContext


class WorldPatrolEntry:

    def __init__(self, entry_name: str, entry_id: str):
        self.entry_name: str = entry_name
        self.entry_id: str = entry_id


class WorldPatrolArea:

    def __init__(
            self,
            entry: WorldPatrolEntry,
            area_name: str,
            area_id: str,
    ):
        self.entry: WorldPatrolEntry = entry
        self.area_name: str = area_name
        self.area_id: str = area_id

        self.parent_area: "WorldPatrolArea | None" = None
        self.sub_area_list: "list[WorldPatrolArea] | None" = None

    @cached_property
    def full_id(self) -> str:
        if self.parent_area is None:
            return self.area_id
        else:
            return f'{self.parent_area.full_id}_{self.area_id}'

    @cached_property
    def full_name(self) -> str:
        if self.parent_area is None:
            return self.area_name
        else:
            return f'{self.parent_area.full_name}_{self.area_name}'


class WorldPatrolLargeMapIcon:

    def __init__(
            self,
            icon_name: str,
            template_id: str,
            lm_pos: list[int],
            tp_pos: list[int] | None,
    ):
        self.icon_name: str = icon_name
        self.template_id: str = template_id
        self.lm_pos: Point = Point(lm_pos[0], lm_pos[1])  # 图标中心点在大地图上的坐标
        self.tp_pos: Point = self.lm_pos if tp_pos is None else Point(tp_pos[0], tp_pos[1])  # 传送落地后的坐标

    def to_dict(self) -> dict:
        return {
            'icon_name': self.icon_name,
            'template_id': self.template_id,
            'lm_pos': [self.lm_pos.x, self.lm_pos.y],
            'tp_pos': [self.tp_pos.x, self.tp_pos.y],
        }


class WorldPatrolLargeMap:

    def __init__(
            self,
            area_full_id: str,
            road_mask: MatLike,
            icon_list: list[WorldPatrolLargeMapIcon],
    ):
        self.area_full_id: str = area_full_id
        self.road_mask: MatLike = road_mask
        self.icon_list: list[WorldPatrolLargeMapIcon] = icon_list

    def to_dict(self) -> dict:
        return {
            'area_full_id': self.area_full_id,
            'icon_list': [i.to_dict() for i in self.icon_list],
        }

def world_patrol_dir():
    return os_utils.get_path_under_work_dir('assets', 'game_data', 'world_patrol')


def entry_dir(entry: WorldPatrolEntry):
    return os_utils.get_path_under_work_dir('assets', 'game_data', 'world_patrol', entry.entry_id)


def area_dir(area: WorldPatrolArea):
    return os_utils.get_path_under_work_dir('assets', 'game_data', 'world_patrol', area.entry.entry_id, area.full_id)


def road_mask_path(area: WorldPatrolArea):
    return os.path.join(area_dir(area), 'road_mask.png')


def icon_yaml_path(area: WorldPatrolArea):
    return os.path.join(area_dir(area), 'icon.yml')


class WorldPatrolService:

    def __init__(self, ctx: ZContext):
        self.ctx: ZContext = ctx

        self.entry_list: list[WorldPatrolEntry] = []
        self.area_list: list[WorldPatrolArea] = []
        self.large_map_list: list[WorldPatrolLargeMap] = []

    def load_data(self):
        self.load_entry()
        self.load_area()
        self.load_area_map()

    def load_entry(self):
        self.entry_list = []
        file_path = os.path.join(world_patrol_dir(), 'world_patrol_entry.yml')

        op = YamlOperator(file_path)
        for i in op.data:
            self.entry_list.append(WorldPatrolEntry(i['entry_name'], i['entry_id']))

    def load_area(self):
        self.area_list = []
        for entry in self.entry_list:
            file_path = os.path.join(entry_dir(entry), 'map_area.yml')
            op = YamlOperator(file_path)
            for i in op.data:
                area = WorldPatrolArea(entry, i['area_name'], i['area_id'])

                if 'sub_area_list' in i:
                    area.sub_area_list = []
                    for j in i['sub_area_list']:
                        sub_area = WorldPatrolArea(entry, j['area_name'], j['area_id'])
                        sub_area.parent_area = area
                        area.sub_area_list.append(sub_area)
                        self.area_list.append(sub_area)

                self.area_list.append(area)

    def load_area_map(self):
        self.large_map_list = []
        for area in self.area_list:
            road_mask = cv2_utils.read_image(road_mask_path(area))
            if road_mask is None:
                continue

            icon_data = YamlOperator(icon_yaml_path(area)).data
            icon_list: list[WorldPatrolLargeMapIcon] = []
            for i in icon_data:
                icon_list.append(WorldPatrolLargeMapIcon(
                    icon_name=i.get('icon_name', ''),
                    template_id=i.get('template_id', ''),
                    lm_pos=i.get('lm_pos', None),
                    tp_pos=i.get('tp_pos', None),
                ))

            lm = WorldPatrolLargeMap(area.full_id, road_mask, icon_list)
            self.large_map_list.append(lm)

    def get_area_list_by_entry(self, entry: WorldPatrolEntry) -> list[WorldPatrolArea]:
        return [i for i in self.area_list if i.entry.entry_id == entry.entry_id]

    def get_large_map_by_area_full_id(self, area_full_id: str) -> WorldPatrolLargeMap | None:
        for i in self.large_map_list:
            if i.area_full_id == area_full_id:
                return i
        return None

    def save_world_patrol_large_map(self, area: WorldPatrolArea, large_map: WorldPatrolLargeMap) -> bool:
        """
        保存一个区域的地图

        Args:
            area: 区域
            large_map: 地图

        Returns:
            bool: 是否保存成功
        """
        if area is None or large_map is None:
            log.error('area or large_map is None')
            return False
        if area.full_id != large_map.area_full_id:
            log.error('area full ids are not same')
            return False

        cv2_utils.save_image(large_map.road_mask, road_mask_path(area))

        op = YamlOperator(icon_yaml_path(area))
        op.data = [i.to_dict() for i in large_map.icon_list]
        op.save()

        log.info(f'保存区域地图成功: {area.full_id}')
        self.load_area_map()
        return True

    def delete_world_patrol_large_map(self, area: WorldPatrolArea) -> bool:
        """
        删除一个区域的地图

        Args:
            area: 区域

        Returns:
            bool: 是否删除成功
        """
        target = None
        for i in self.large_map_list:
            if i.area_full_id == area.full_id:
                target = i
                break

        if target is None:
            return False

        self.large_map_list.remove(target)

        if os.path.exists(road_mask_path(area)):
            os.remove(road_mask_path(area))

        if os.path.exists(icon_yaml_path(area)):
            os.remove(icon_yaml_path(area))
        return True
