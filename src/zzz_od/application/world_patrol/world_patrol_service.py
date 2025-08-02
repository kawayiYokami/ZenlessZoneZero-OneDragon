import os

import yaml

from one_dragon.base.config.yaml_operator import YamlOperator
from one_dragon.base.geometry.point import Point
from one_dragon.utils import os_utils, cv2_utils
from one_dragon.utils.log_utils import log
from zzz_od.application.world_patrol.world_patrol_area import WorldPatrolArea, WorldPatrolEntry, WorldPatrolLargeMap, \
    world_patrol_dir, entry_dir, road_mask_path, icon_yaml_path, WorldPatrolLargeMapIcon
from zzz_od.application.world_patrol.world_patrol_route import WorldPatrolRoute, WorldPatrolOpType
from zzz_od.context.zzz_context import ZContext


def area_route_dir(area: WorldPatrolArea):
    return os_utils.get_path_under_work_dir('assets', 'config', 'world_patrol_route', 'system',
                                            area.entry.entry_id, area.full_id)


class WorldPatrolService:

    def __init__(self, ctx: ZContext):
        self.ctx: ZContext = ctx

        self.entry_list: list[WorldPatrolEntry] = []
        self.area_list: list[WorldPatrolArea] = []
        self.large_map_list: list[WorldPatrolLargeMap] = []
        self.route_list: list[WorldPatrolRoute] = []

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

    def get_world_patrol_routes(self, area: WorldPatrolArea) -> list[WorldPatrolRoute]:
        """获取指定区域的所有路线"""
        routes = []
        route_dir = area_route_dir(area)

        if not os.path.exists(route_dir):
            return routes

        # 遍历路线文件夹中的所有yml文件
        for filename in os.listdir(route_dir):
            if filename.endswith('.yml'):
                try:
                    file_path = os.path.join(route_dir, filename)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f)
                    route = WorldPatrolRoute.from_dict(data, area)
                    routes.append(route)
                except Exception as e:
                    log.error(f'加载路线文件失败: {filename}, 错误: {e}')

        # 按idx排序
        routes.sort(key=lambda r: r.idx)
        return routes

    def save_world_patrol_route(self, route: WorldPatrolRoute) -> bool:
        """保存世界巡逻路线"""
        try:
            route_dir = area_route_dir(route.tp_area)
            os.makedirs(route_dir, exist_ok=True)

            filename = f'{route.idx:02d}.yml'
            file_path = os.path.join(route_dir, filename)

            with open(file_path, 'w', encoding='utf-8') as f:
                yaml.dump(route.to_dict(), f, default_flow_style=False, allow_unicode=True)

            log.info(f'保存路线成功: {route.tp_area.full_name} - {route.tp_name} ({filename})')
            return True
        except Exception as e:
            log.error(f'保存路线失败: {e}')
            return False

    def get_next_route_idx(self, area: WorldPatrolArea) -> int:
        """获取指定区域的下一个路线索引"""
        routes = self.get_world_patrol_routes(area)
        if not routes:
            return 1
        return max(route.idx for route in routes) + 1

    def delete_world_patrol_route(self, route: WorldPatrolRoute) -> bool:
        """删除世界巡逻路线"""
        try:
            route_dir = area_route_dir(route.tp_area)
            filename = f'{route.idx:02d}.yml'
            file_path = os.path.join(route_dir, filename)

            if os.path.exists(file_path):
                os.remove(file_path)
                log.info(f'删除路线成功: {route.tp_area.full_name} - {route.tp_name} ({filename})')
                return True
            else:
                log.error(f'路线文件不存在: {filename}')
                return False

        except Exception as e:
            log.error(f'删除路线失败: {e}')
            return False

    def get_route_last_pos(self, route: WorldPatrolRoute) -> Point | None:
        """
        获取路线的最后一个点坐标

        Args:
            route: 路线

        Returns:
            Point: 最后一个点坐标
        """
        large_map = None
        for lm in self.large_map_list:
            if lm.area_full_id == route.tp_area.full_id:
                large_map = lm
                break
        if large_map is None:
            return None

        tp_icon = None
        for icon in large_map.icon_list:
            if icon.icon_name == route.tp_name:
                tp_icon = icon
                break
        if tp_icon is None:
            return None

        current_pos = tp_icon.tp_pos
        for op in route.op_list:
            if op.op_type in [
                WorldPatrolOpType.MOVE
            ]:
                current_pos = Point(int(op.data[0]), int(op.data[1]))

        return current_pos
