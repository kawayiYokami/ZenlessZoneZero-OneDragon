from __future__ import annotations

import time
from dataclasses import dataclass, replace
from math import asin, atan2, degrees, hypot
from typing import TYPE_CHECKING, Literal

import cv2

from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.utils import cal_utils, cv2_utils
from one_dragon.utils.log_utils import log
from zzz_od.application.bagel.bagel_const import (
    CONTAINER_ARRIVE_STATUS,
    CONTAINER_LABELS,
    CONTAINER_PROMPT_AREAS,
    NAV_CAMERA_EFFECT_WAIT,
    NAV_CAMERA_STABLE_DELTA,
    NAV_CAMERA_STABLE_INTERVAL,
    NAV_CAMERA_TURN_WAIT,
    NAV_CAMERA_WAIT_TIMEOUT,
    NAV_CRUISE_DISTANCE,
    NAV_CRUISE_TURN_CAP,
    NAV_CRUISE_TURN_GAP,
    NAV_CRUISE_TURN_LIMIT,
    NAV_DIRECTION_MAX_CROSS_TRACK,
    NAV_DIRECTION_TURN_LIMIT,
    NAV_FAST_TURN_REMAINDER,
    NAV_FORWARD_PRESS,
    NAV_INITIAL_LOCATE_MISS_LIMIT,
    NAV_INITIAL_LOCATE_WAIT,
    NAV_LOCATE_MISS_LIMIT,
    NAV_NEAR_TURN_LIMIT,
    NAV_PASS_FORWARD_PRESS,
    NAV_PASS_FRAME_WAIT,
    NAV_PATH_LOOKAHEAD,
    NAV_SAFE_APPROACH_DISTANCE,
    NAV_SAFE_APPROACH_PRESS,
    NAV_SAFE_BRAKE_WAIT,
    NAV_SPRINT_BRAKE_WAIT,
    NAV_START_SPRINT_MIN_DISTANCE,
    NAV_START_SPRINT_PRESS,
    NAV_STOP_TURN_CAP,
    NAV_TURN_DEADBAND,
)
from zzz_od.application.bagel.bagel_container import ContainerRecovery, container_state
from zzz_od.application.bagel.bagel_flow import NavigationOptions, load_published_flow
from zzz_od.application.bagel.bagel_movement import BagelMovement
from zzz_od.application.bagel.bagel_operation import (
    BagelOperation,
    BagelRecoverableFailure,
)
from zzz_od.application.bagel.bagel_route import BagelRoute
from zzz_od.application.bagel.bagel_route_vision import BagelRouteVision
from zzz_od.application.bagel.bagel_turn_compensation import BagelTurnCompensator

if TYPE_CHECKING:
    from collections.abc import Callable

    from cv2.typing import MatLike

    from one_dragon.base.operation.operation_base import OperationResult
    from one_dragon.base.operation.operation_round_result import OperationRoundResult
    from zzz_od.application.bagel.bagel_fixed_map import BagelFixedMap
    from zzz_od.context.zzz_context import ZContext

Destination = Literal['box', 'safe', 'mech', 'move']


@dataclass
class _PendingCameraTurn:
    """等待新镜头观测确认的命令；角度沿用控制器的逆时针约定。"""

    source_angle: float
    commanded_angle: float
    sent_at: float
    started_at: float
    learn: bool = False
    source_position: tuple[float, float] | None = None
    feedback_angle: float | None = None
    feedback_time: float | None = None


class BagelNavigate(BagelOperation):
    """按实际定位朝目的地正常移动或碎步接近，按坐标或交互提示完成。"""

    STATUS_UNSUPPORTED: str = '非支持出生点'
    STATUS_WAYPOINT: str = '已到路点'
    STATUS_ARRIVED_BOX: str = '已到武备箱'
    STATUS_ARRIVED_SAFE: str = '已到电子保险箱'
    STATUS_ARRIVED_MECH: str = '已到机械保险箱'

    def __init__(
        self,
        ctx: ZContext,
        destination: str = 'box',
        map_id: str = 'janus_high_a',
        route_data: BagelRoute | None = None,
        on_observation: Callable[[dict[str, object]], None] | None = None,
        navigation: NavigationOptions | None = None,
        require_spawn: bool = True,
        check_target_position: bool = False,
        coordinate_only: bool = False,
        final_approach: bool = False,
        map_snapshot: BagelFixedMap | None = None,
        recovery: ContainerRecovery | None = None,
        recovering: bool = False,
        on_location_wait: Callable[[], None] | None = None,
        turn_compensator: BagelTurnCompensator | None = None,
        approach_from: tuple[float, float] | None = None,
        on_waypoint_reached: Callable[[int], None] | None = None,
    ) -> None:
        """普通移动使用独立坐标；只有靠近容器才需要目标类型。"""
        if destination not in (*CONTAINER_LABELS, 'move'):
            raise ValueError('导航目标必须是普通移动或已支持的容器')
        if destination == 'move' and (route_data is None or not coordinate_only):
            raise ValueError('普通移动必须提供独立路点并按坐标完成')
        route_label = {
            'janus_high_a': '录像店复活点',
            'janus_high_b': '白鸽工地地铁站复活点',
            'janus_high_c': '单廊双容器复活点',
        }.get(map_id, map_id)
        name = f'贝果-{route_label}前往{CONTAINER_LABELS.get(destination, "路点")}'
        if destination == 'move':
            name = '贝果-移动到路点'
        default_step = None
        if route_data is None:
            points = []
            for step in load_published_flow(map_id).steps:
                if step.action == 'move':
                    # 兼容直接导航到容器的旧入口；独立流程执行不经过这里。
                    points.extend(replace(p, stage=destination) for p in step.waypoints)
                    continue
                if step.action != 'approach':
                    continue
                if step.target != destination:
                    points.clear()
                    continue
                for point in step.waypoints:
                    if not points or point != points[-1]:
                        points.append(point)
                if step.action == 'approach':
                    default_step = step
                    break
            if default_step is None:
                raise ValueError('发布流程没有对应的移动步骤')
            route_data = BagelRoute(map_id, tuple(points))
        self.navigation: NavigationOptions = navigation or (default_step.navigation if default_step else NavigationOptions())
        self.require_spawn: bool = require_spawn
        self.check_target_position: bool = check_target_position
        self.coordinate_only: bool = coordinate_only
        self.final_approach: bool = final_approach
        self.on_waypoint_reached: Callable[[int], None] | None = on_waypoint_reached
        self._waypoint_timeout: float | None = (
            self.navigation.effective_timeout(destination) if on_waypoint_reached is not None else None
        )
        timeout = self.navigation.effective_timeout(destination)
        if on_waypoint_reached is not None:
            if not coordinate_only or destination != 'move':
                raise ValueError('连续路点回调只用于普通移动步骤')
            timeout *= len(route_data.points_for(destination))
        super().__init__(ctx, op_name=name, timeout_seconds=timeout)
        self.destination: Destination = destination
        self.map_id: str = map_id
        self.on_observation: Callable[[dict[str, object]], None] | None = on_observation
        self.vision: BagelRouteVision = BagelRouteVision(map_id, route_data, map_snapshot)
        if not self.vision.route.points_for(destination):
            raise ValueError(f'{map_id} 暂不支持该导航路段：{destination}')
        self.active_waypoints: list[tuple[str, tuple[float, float]]] = self._select_waypoints()
        self.interact_area: str = CONTAINER_PROMPT_AREAS.get(destination, '电子保险箱交互')
        self.forward_press: float = NAV_FORWARD_PRESS
        self.arrive_status: str = CONTAINER_ARRIVE_STATUS.get(destination, self.STATUS_WAYPOINT)
        self.waypoint_index: int = 0
        self.steps: int = 0
        self.movement: BagelMovement = BagelMovement(ctx.controller)
        self._waypoint_origin: tuple[float, float] | None = None
        self._waypoint_origin_index: int = -1
        self._waypoint_started: float = 0
        self._coordinate_recovering: bool = False
        self._sprinted_waypoints: set[int] = set()
        self._sprint_braked_waypoints: set[int] = set()
        self._sprint_probe: tuple[tuple[float, float], float] | None = None
        self._directional_cruise_index: int = -1
        self._directional_cruise_enabled: bool = False
        # 表示已开始依据有效镜头移动；侧移时镜头不必朝向目标。
        self.heading_aligned: bool = False
        self.last_input_frame: float | None = None
        self.pending_turn: _PendingCameraTurn | None = None
        self._heading_wait_started: float | None = None
        self._turn_feedback_after: float | None = None
        self._owns_turn_compensator: bool = turn_compensator is None
        self.turn_compensator: BagelTurnCompensator = turn_compensator or BagelTurnCompensator(ctx.controller)
        self.approach_from: tuple[float, float] | None = approach_from
        self._approach_heading_active: bool = False
        self.target_wait_started: float | None = None
        self.locate_misses: int = 0
        self.initial_locate_misses: int = 0
        self.last_position: tuple[float, float] | None = None
        self._cruise_progress: tuple[tuple[float, float], float] | None = None
        self.last_cruise_turn_at: float | None = None
        self._spawn_far: int = 0
        self._spawn_miss: int = 0
        self._safe_brake_until: float | None = None
        self._destination_braked: bool = False
        self._owns_recovery: bool = recovery is None
        self.recovery: ContainerRecovery = recovery or ContainerRecovery(lambda: self.operation_usage_time)
        self.recovering: bool = recovering
        self.on_location_wait: Callable[[], None] | None = on_location_wait
        self._settle_until: float | None = None
        self._settled: bool = False

    def _select_waypoints(self) -> list[tuple[str, tuple[float, float]]]:
        """按显式路段选点，名称仅用于显示。"""
        return [(point.name, point.xy) for point in self.vision.route.points_for(self.destination)]

    def handle_init(self) -> None:
        """每次执行从起点检查开始。"""
        super().handle_init()
        self._release_forward()
        self.waypoint_index = len(self.active_waypoints) - 1 if self.final_approach else 0
        self.steps = 0
        self.target_wait_started = None
        self.locate_misses = 0
        self.initial_locate_misses = 0
        self.last_position = None
        self._cruise_progress = None
        self.last_cruise_turn_at = None
        self._spawn_far = 0
        self._spawn_miss = 0
        self._safe_brake_until = None
        self._destination_braked = False
        self._settle_until = None
        self._settled = False
        if self._owns_recovery:
            self.recovery = ContainerRecovery(lambda: self.operation_usage_time)
        self._invalidate_heading()
        if self._owns_turn_compensator:
            self.turn_compensator.reset()
        self.turn_compensator.check_conditions()
        self._approach_heading_active = False
        self._waypoint_started = self.operation_usage_time
        self._coordinate_recovering = False
        self._sprinted_waypoints.clear()
        self._sprint_braked_waypoints.clear()
        self._sprint_probe = None
        log.info('贝果导航：目标=%s，重新靠近=%s；路线快照=%s',
                 self.destination, self.recovering, self.vision.route.to_dict())

    def minimap(self) -> MatLike:
        """按原生区域裁图，并将框架 RGB 截图转为定位模块的 BGR。"""
        area = self.ctx.screen_loader.get_area('贝果-局内', '定位小地图')
        if area is None:
            raise ValueError('缺少贝果定位小地图区域')
        crop = cv2_utils.crop_image_only(self.last_screenshot, area.pc_rect)
        return cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)

    @operation_node(name='检查起点', is_start_node=True, node_max_retry_times=6)
    def check_start(self) -> OperationRoundResult:
        """到箱前须确认本路线出生点；到大保险前只需确认局内 HUD。

        配准没有结果时再看。连续三帧没有位置，或连续三帧离开出生点超过 5 像素，才当作不是这条路线。
        """
        if self.is_bagel_result():
            return self.round_fail(self.STATUS_DEFEATED)
        if not self.coordinate_only:
            state = container_state(self, self.destination)
            if state == 'ready':
                return self.round_success('开始移动')
            if state == 'panel':
                self._release_forward()
                self.recovery.start()
                reason = self.recovery.error()
                return self.round_recoverable_fail(reason) if reason else self.round_wait('等待容器面板确认', wait=0.25)
        if not self.round_by_find_area(self.last_screenshot, '战斗画面', '按键-普通攻击').is_success:
            return self.round_recoverable_fail('未识别贝果局内画面')
        if self.destination not in ('box', 'mech') or not self.require_spawn:
            if self.vision.locate(self.minimap()) is None:
                reason = '起点小地图暂时无法定位'
                return self.round_retry(reason, data=BagelRecoverableFailure(reason), wait=0.15)
            return self.round_success('开始移动')
        position = self.vision.locate(self.minimap())
        if position is None:
            self._spawn_far = 0
            self._spawn_miss += 1
            if self._spawn_miss >= 3:
                return self.round_success(self.STATUS_UNSUPPORTED)
            return self.round_retry('小地图暂时对不上出生点', wait=0.15)
        self._spawn_miss = 0
        distance = hypot(position[0] - self.vision.spawn[0], position[1] - self.vision.spawn[1])
        if distance <= 5:
            self._spawn_far = 0
            return self.round_success('开始移动')
        self._spawn_far += 1
        if self._spawn_far >= 3:
            return self.round_success(self.STATUS_UNSUPPORTED)
        return self.round_retry('出生点位置未稳定', wait=0.15)

    @node_from(from_name='检查起点', status='开始移动')
    @operation_node(name='沿小地图节点移动')
    def move_to_target(self) -> OperationRoundResult:
        """两种方式均朝目的地调整；靠近步骤到点后只等待交互提示。"""
        if self._waypoint_timeout is not None and self.operation_usage_time - self._waypoint_started >= self._waypoint_timeout:
            self._release_forward()
            return self.round_recoverable_fail('当前移动步骤超时，停止移动')
        if self.is_bagel_result():
            self._release_forward()
            return self.round_fail(self.STATUS_DEFEATED)
        if not self.coordinate_only:
            state = container_state(self, self.destination)
            if state == 'ready':
                self._release_forward()
                return self.round_success(self.arrive_status)
            reason = self.recovery.error()
            if reason:
                self._release_forward()
                return self.round_recoverable_fail(reason)
            if state == 'panel':
                self._release_forward()
                self._discard_turn_feedback()
                self.recovery.start()
                return self.round_wait('等待容器面板确认', wait=0.25)
        if self.last_input_frame is not None and self.last_screenshot_time <= self.last_input_frame:
            return self.round_wait('等待动作后的新截图', wait=0.15)
        if self._settle_until is not None:
            self._release_forward()
            if self.last_screenshot_time < self._settle_until:
                return self.round_wait('等待容器前停稳后的新截图', wait=0.15)
            self._settle_until = None
            self._settled = True
        if self._safe_brake_until is not None:
            self._release_forward()
            if self.last_screenshot_time < self._safe_brake_until:
                return self.round_wait('等待移动停稳', wait=0.15)
            self._safe_brake_until = None
        has_interaction = (
            not self.coordinate_only
            and self.round_by_find_area(self.last_screenshot, '贝果-局内', self.interact_area).is_success
            and self.round_by_find_area(self.last_screenshot, '贝果-局内', '交互F键').is_success
        )
        if has_interaction and not self._settled:
            self._release_forward()
            self._discard_turn_feedback()
            self.recovery.start()
            self.last_input_frame = max(time.time(), self.last_screenshot_time)
            self._settle_until = self.last_input_frame + NAV_SAFE_BRAKE_WAIT
            return self.round_wait('发现容器提示，松键后确认停稳', wait=NAV_SAFE_BRAKE_WAIT)
        if not self.round_by_find_area(self.last_screenshot, '战斗画面', '按键-普通攻击').is_success:
            self._release_forward()
            self._discard_turn_feedback()
            if self.round_by_find_area(self.last_screenshot, '战斗-菜单', '按钮-退出战斗').is_success:
                return self.round_recoverable_fail('移动中打开了暂停菜单')
            return self.round_wait('移动后暂未识别局内 HUD', wait=0.25)
        crop = self.minimap()
        position = self.vision.locate(crop)
        camera_heading = self.vision.camera_heading(crop)
        log.debug('贝果导航定位 map=%s stage=%s point=%s xy=%s', self.map_id, self.destination, self.waypoint_index, position)
        log.debug(
            '贝果镜头观测：map=%s point=%s screenshot=%.6f xy=%s camera=%s reason=%s quality=%s scale=%.3f',
            self.map_id, self.waypoint_index, self.last_screenshot_time, position,
            camera_heading.angle, camera_heading.reason, camera_heading.quality, self.turn_compensator.scale,
        )
        if self.on_observation is not None:
            self.on_observation({
                'time': self.last_screenshot_time, 'position': position,
                'angle': self.vision.player_angle(crop), 'target': self.waypoint_index,
                'camera_angle': camera_heading.angle,
                'camera_reason': camera_heading.reason,
                'camera_quality': camera_heading.quality,
                'turn_command_angle': self.pending_turn.commanded_angle if self.pending_turn else None,
                'turn_sent_at': self.pending_turn.sent_at if self.pending_turn else None,
                'map_snapshot_id': self.vision.map.snapshot_id,
                'location_reason': self.vision.last_location.reason if self.vision.last_location else None,
                'location_ms': self.vision.last_location.elapsed_ms if self.vision.last_location else None,
                'location_inliers': self.vision.last_location.inliers if self.vision.last_location else None,
                'location_residual_px': self.vision.last_location.median_residual_px if self.vision.last_location else None,
            })
        small_steps = (
            (self.recovering or self.navigation.effective_final_mode(self.destination) in ('small_steps', 'short_steps'))
            and self.waypoint_index == len(self.active_waypoints) - 1
        )
        if position is None:
            self._release_forward()
            self._discard_turn_feedback()
            self._heading_wait_started = None
            if not self.coordinate_only and self.vision.last_location is not None and self.vision.last_location.reason in (
                'ambiguous_position', 'outside_coverage', 'invalid_crop',
            ):
                return self.round_recoverable_fail(f'小地图定位失败：{self.vision.last_location.reason}')
            if self.last_position is None and self.vision.last_location is not None:
                self._release_forward()
                if self.vision.last_location.reason == 'insufficient_geometry':
                    self.initial_locate_misses += 1
                    if self.initial_locate_misses <= NAV_INITIAL_LOCATE_MISS_LIMIT:
                        if self.on_location_wait is not None:
                            self.on_location_wait()
                        return self.round_wait(
                            '起步小地图暂时无法定位，等待下一帧', wait=NAV_INITIAL_LOCATE_WAIT,
                        )
                return self.round_recoverable_fail('小地图定位失败，停止移动')
            if self.last_position is not None and self.locate_misses < NAV_LOCATE_MISS_LIMIT:
                self.locate_misses += 1
                self._release_forward()
                if self.on_location_wait is not None:
                    self.on_location_wait()
                return self.round_wait('小地图暂时对不上，再看一帧', wait=0.15)
            self._release_forward()
            return self.round_recoverable_fail('小地图定位失败，停止移动')
        self.locate_misses = 0
        self.initial_locate_misses = 0
        self.last_position = position
        if self._waypoint_origin_index != self.waypoint_index:
            self._waypoint_origin = position
            self._waypoint_origin_index = self.waypoint_index
            self._coordinate_forward_only = False
        final_xy = self.active_waypoints[-1][1]
        final_distance = hypot(position[0] - final_xy[0], position[1] - final_xy[1])
        if not self.coordinate_only and has_interaction:
            self._release_forward()
            if final_distance > self.navigation.effective_interaction_distance:
                return self.round_recoverable_fail('容器交互提示与当前目标位置不符')
            return self.round_success(self.arrive_status)
        if not self.coordinate_only and self.waypoint_index == len(self.active_waypoints) - 1:
            if final_distance <= self.navigation.effective_interaction_distance:
                self.recovery.start()
            if self._settled:
                self._release_forward()
                reason = self.recovery.request_approach()
                if reason:
                    return self.round_recoverable_fail(reason)
                self.recovering = True
                small_steps = True
                self._settled = False
                self._invalidate_heading()
        if self.target_wait_started is not None:
            return self._wait_for_interaction()
        if self.destination in ('safe', 'mech', 'move') and self._cruise_progress is not None:
            previous, started = self._cruise_progress
            if hypot(position[0] - previous[0], position[1] - previous[1]) >= 2:
                self._cruise_progress = (position, self.last_screenshot_time)
            elif self.last_screenshot_time - started >= 3:
                self._release_forward()
                return self.round_recoverable_fail('持续前进但位置未变化，停止移动')
        name, target = self.active_waypoints[self.waypoint_index]
        distance = hypot(target[0] - position[0], target[1] - position[1])
        point = self.vision.route.points_for(self.destination)[self.waypoint_index]
        if (
            self.coordinate_only and self.waypoint_index in self._sprinted_waypoints
            and self.waypoint_index not in self._sprint_braked_waypoints
            and point.arrival_radius < distance <= NAV_CRUISE_DISTANCE
        ):
            self._sprint_braked_waypoints.add(self.waypoint_index)
            self._release_forward()
            self._record_input()
            assert self.last_input_frame is not None
            self._safe_brake_until = self.last_input_frame + NAV_SPRINT_BRAKE_WAIT
            return self.round_wait('冲刺后临近路点，先减速再核对位置', wait=NAV_SPRINT_BRAKE_WAIT)
        brake_before_safe = (
            self.destination == 'safe'
            and (self.waypoint_index == len(self.active_waypoints) - 2
                 or (self.coordinate_only and point.role == 'approach'))
        )
        if (
            ((brake_before_safe
              and self.navigation.effective_final_mode(self.destination) == 'short_steps')
             or (self.destination == 'move' and self.navigation.brake_distance is not None))
            and distance <= self.navigation.effective_brake_distance
            and abs(position[1] - target[1]) <= NAV_SAFE_APPROACH_DISTANCE
            and not self._destination_braked
        ):
            # 已沿墙接近时提前松键；必须等停稳后的新截图，不能用旧帧直接补一步。
            self._release_forward()
            if self.coordinate_only:
                # 提前松键不是到达；停稳后继续本步骤，避免反复停车或恢复持续前进。
                self._destination_braked = True
                self.last_input_frame = self.last_screenshot_time
                self._safe_brake_until = self.last_screenshot_time + NAV_SAFE_BRAKE_WAIT
                return self.round_wait('提前松键，等待停稳后核对位置', wait=NAV_SAFE_BRAKE_WAIT)
            self.waypoint_index += 1
            self.last_input_frame = self.last_screenshot_time
            self._safe_brake_until = self.last_screenshot_time + NAV_SAFE_BRAKE_WAIT
            return self.round_wait('保险箱前松键，等待停稳后碎步', wait=NAV_SAFE_BRAKE_WAIT)
        # 巷口提前切段；楼角和贴墙中间点收紧距离，避免抄近路穿过停车位。
        intermediate = self.waypoint_index + 1 < len(self.active_waypoints)
        safe_corner = self.destination == 'safe' and intermediate
        point = self.vision.route.points_for(self.destination)[self.waypoint_index]
        if (intermediate or self.coordinate_only) and self._corner_is_behind(
            position, target, distance, point.passed_radius,
        ):
            if point.stop:
                self._release_forward()
                self._discard_turn_feedback()
            if self.coordinate_only:
                return self._advance_coordinate_waypoint(position, camera_heading.angle)
            self.waypoint_index += 1
            return self.round_wait('已越过中间节点，准备下一段', wait=0.1)
        arrival_radius = point.arrival_radius
        if distance <= (min(arrival_radius, 0.5) if self.recovering else arrival_radius):
            if point.stop or not intermediate:
                self._release_forward()
            if self.coordinate_only:
                return self._advance_coordinate_waypoint(position, camera_heading.angle)
            if self.waypoint_index + 1 == len(self.active_waypoints):
                self.target_wait_started = self.last_screenshot_time
                return self._wait_for_interaction()
            self.waypoint_index += 1
            return self.round_wait('已到中间节点，准备下一段', wait=0.1)
        self.target_wait_started = None
        reason = self.recovery.error() if not self.coordinate_only else None
        if reason:
            self._release_forward()
            return self.round_recoverable_fail(reason)
        angle = camera_heading.angle
        if angle is None:
            return self._wait_for_camera_heading(camera_heading.reason)
        self._heading_wait_started = None
        target_angle = self._target_heading(position, target, distance)
        if (
            self.coordinate_only and self.movement.supports_directions
            and not self._destination_braked
            and distance > NAV_CRUISE_DISTANCE
        ):
            return self._follow_coordinate_path(angle, name)
        if self.coordinate_only:
            # 临近路点先减速，不能在等待镜头反馈期间继续持键越过转角。
            self._release_forward()
        feedback_wait = self._wait_for_turn_feedback(angle, target_angle, distance)
        if feedback_wait is not None:
            return feedback_wait
        if not small_steps and not self._destination_braked and (safe_corner or distance > NAV_CRUISE_DISTANCE):
            return self._cruise_toward(angle, target_angle, name)
        limited = self._fail_if_action_limit()
        if limited is not None:
            return limited
        heading_tolerance = NAV_TURN_DEADBAND
        if self.coordinate_only and distance <= NAV_CRUISE_DISTANCE:
            # 对准到达范围即可；保留两成余量，到达仍由原半径和当前定位确认。
            heading_tolerance = min(
                NAV_NEAR_TURN_LIMIT,
                max(NAV_TURN_DEADBAND, degrees(asin(min(1.0, 0.8 * arrival_radius / distance)))),
            )
        movement_sector = 0
        if self.coordinate_only and self.movement.supports_directions:
            relative = cal_utils.angle_delta((-angle) % 360, target_angle)
            movement_sector = self._select_movement_sector(relative)
        adjustment = self._align_to_heading(angle, target_angle, heading_tolerance, movement_sector)
        if adjustment is not None:
            return adjustment
        reason = self.recovery.error() if not self.coordinate_only else None
        if reason:
            self._release_forward()
            return self.round_recoverable_fail(reason)
        passing = self.coordinate_only and intermediate and not point.stop
        press_time = NAV_SAFE_APPROACH_PRESS if small_steps else (NAV_PASS_FORWARD_PRESS if passing else self.forward_press)
        self._release_forward()
        self.movement.pulse(movement_sector, press_time)
        self.turn_compensator.note_motion()
        self._record_input()
        return self.round_wait(f'前往{name}', wait=NAV_PASS_FRAME_WAIT if passing else 0.15)

    def _advance_coordinate_waypoint(
        self, position: tuple[float, float], image_angle: float | None,
    ) -> OperationRoundResult:
        """进入原到达范围后切段；连续普通步骤在同一帧更新移动键。"""
        old_index = self.waypoint_index
        if old_index + 1 == len(self.active_waypoints):
            self._release_forward()
            return self.round_success(self.STATUS_WAYPOINT)
        stopped = self.vision.route.points_for(self.destination)[old_index].stop
        self.waypoint_index += 1
        self._waypoint_origin = position
        self._waypoint_origin_index = self.waypoint_index
        self._coordinate_recovering = False
        self._cruise_progress = (position, self.last_screenshot_time)
        self._waypoint_started = self.operation_usage_time
        self._sprint_probe = None
        if self.on_waypoint_reached is not None:
            self.steps = 0
            self.on_waypoint_reached(old_index)
        if stopped:
            self._release_forward()
            self._discard_turn_feedback()
            return self.round_wait('已到停步路点，重新观察下一段', wait=0.1)
        if image_angle is None:
            return self._wait_for_camera_heading('过点时镜头不可用')
        name, target = self.active_waypoints[self.waypoint_index]
        next_point = self.vision.route.points_for(self.destination)[self.waypoint_index]
        distance = hypot(target[0] - position[0], target[1] - position[1])
        if (
            not self.movement.supports_directions
            or distance <= next_point.arrival_radius
            or (distance <= NAV_CRUISE_DISTANCE and next_point.stop)
        ):
            self._release_forward()
            return self.round_wait('已到中间路点，观察下一段')
        return self._follow_coordinate_path(image_angle, name)

    def _path_headings(self, lookahead: float = NAV_PATH_LOOKAHEAD) -> tuple[float, float, float]:
        """返回前方短距离目标方向、路段方向和横向偏差；前视点不越过本段终点。"""
        assert self.last_position is not None and self._waypoint_origin is not None
        px, py = self.last_position
        ox, oy = self._waypoint_origin
        tx, ty = self.active_waypoints[self.waypoint_index][1]
        dx, dy = tx - ox, ty - oy
        length = hypot(dx, dy)
        if length < 0.1:
            heading = degrees(atan2(py - ty, tx - px)) % 360
            return heading, heading, 0.0
        progress = ((px - ox) * dx + (py - oy) * dy) / length
        ahead = min(length, max(0.0, progress) + lookahead)
        target_x, target_y = ox + dx * ahead / length, oy + dy * ahead / length
        heading = degrees(atan2(py - target_y, target_x - px)) % 360
        segment_heading = degrees(atan2(-dy, dx)) % 360
        cross_track = abs((px - ox) * dy - (py - oy) * dx) / length
        return heading, segment_heading, cross_track

    def _follow_coordinate_path(self, image_angle: float, name: str) -> OperationRoundResult:
        """移动键按当前位置纠偏，镜头独立调整；只在真实走偏时停车。"""
        heading, _, cross_track = self._path_headings()
        controller_angle = (-image_angle) % 360
        assert self.last_position is not None
        if cross_track > NAV_DIRECTION_MAX_CROSS_TRACK and not self._coordinate_recovering:
            self._coordinate_recovering = True
            self._release_forward()
            self._discard_turn_feedback()
            self._directional_cruise_enabled = False
            self._record_input()
            log.info('贝果路径跟随偏离：横向=%.2f，松键后按新位置短步返回路段', cross_track)
            return self.round_wait('走偏后松键，重新观察位置', wait=NAV_PASS_FRAME_WAIT)
        if self._coordinate_recovering:
            if cross_track <= NAV_DIRECTION_MAX_CROSS_TRACK / 2:
                self._coordinate_recovering = False
            else:
                limited = self._fail_if_action_limit()
                if limited is not None:
                    return limited
                # 只朝当前路段最近点短步，不为恢复前进而停车旋转镜头。
                recovery_heading, _, _ = self._path_headings(0)
                sector = self._select_movement_sector(cal_utils.angle_delta(controller_angle, recovery_heading))
                if self._cruise_progress is None:
                    self._cruise_progress = (self.last_position, self.last_screenshot_time)
                self.movement.pulse(sector, NAV_SAFE_APPROACH_PRESS)
                self.turn_compensator.note_motion()
                self._record_input()
                return self.round_wait('短步返回当前路段', wait=NAV_PASS_FRAME_WAIT)
        sector = self._select_movement_sector(cal_utils.angle_delta(controller_angle, heading))
        self.movement.hold(sector)
        self.heading_aligned = True
        self._directional_cruise_enabled = True
        if self._cruise_progress is None:
            self._cruise_progress = (self.last_position, self.last_screenshot_time)
        if self._maybe_start_sprint(image_angle, heading):
            return self.round_wait('长直线路段冲刺起步')
        # 镜头命令保持原响应窗口；期间继续用新位置和新镜头更新方向键，不学习运动样本。
        turn = self.pending_turn
        if turn is not None:
            if self.last_screenshot_time < turn.sent_at + NAV_CAMERA_EFFECT_WAIT:
                return self.round_wait(f'前往{name}，等待行进镜头反馈')
            self.pending_turn = None
            self._turn_feedback_after = None
        # 镜头和移动键共同朝本段前视点纠偏；只补所选组合的残差，单次目标角差不超过22.5度。
        difference = cal_utils.angle_delta((controller_angle + sector * 45) % 360, heading)
        if abs(difference) > NAV_TURN_DEADBAND and (
            self.last_cruise_turn_at is None
            or self.last_screenshot_time - self.last_cruise_turn_at >= NAV_CRUISE_TURN_GAP
        ):
            limited = self._fail_if_action_limit()
            if limited is not None:
                return limited
            self.last_cruise_turn_at = self.last_screenshot_time
            effective = self.turn_compensator.turn(difference, max_abs_angle_diff=NAV_CRUISE_TURN_CAP)
            self._record_turn(controller_angle, effective)
            return self.round_wait(f'连续移动并调整视角 {difference:.1f}度')
        return self.round_wait(f'连续前往{name}')

    def _target_heading(
        self, position: tuple[float, float], target: tuple[float, float], distance: float,
    ) -> float:
        """容器近点沿已执行的进场路段前进；越界或越点时恢复按实际位置纠偏。"""
        heading = degrees(atan2(position[1] - target[1], target[0] - position[0])) % 360
        active = False
        if self.approach_from is not None and not self.coordinate_only and distance <= NAV_CRUISE_DISTANCE:
            dx, dy = target[0] - self.approach_from[0], target[1] - self.approach_from[1]
            length = hypot(dx, dy)
            if length >= 0.1:
                rx, ry = target[0] - position[0], target[1] - position[1]
                remaining = (rx * dx + ry * dy) / length
                cross_track = abs(rx * dy - ry * dx) / length
                if remaining > 0 and cross_track <= NAV_SAFE_APPROACH_DISTANCE:
                    heading = degrees(atan2(-dy, dx)) % 360
                    active = True
        if active != self._approach_heading_active:
            self._approach_heading_active = active
            log.info('贝果接近方向：沿进场方向=%s，位置=%s，目标=%s，方向=%.1f',
                     active, position, target, heading)
        return heading

    def _wait_for_interaction(self) -> OperationRoundResult:
        """到点后只等待两秒，定位轻微漂移也不会重新起步。"""
        assert self.target_wait_started is not None
        self._release_forward()
        if self.last_screenshot_time - self.target_wait_started < 2:
            return self.round_wait('目标前停步等待交互提示', wait=0.25)
        name = self.active_waypoints[self.waypoint_index][0]
        return self.round_recoverable_fail(f'已到{name}但未发现{self.interact_area}')

    @staticmethod
    def _select_movement_sector(relative_angle: float) -> int:
        """按镜头相对角选择最近的八方向组合。"""
        return round(relative_angle / 45) % 8

    def _directional_cruise_allowed(self, controller_angle: float, target_angle: float) -> bool:
        """只有已对准的组合才能起步侧移；偏离后本段不再重新启用。"""
        relative = cal_utils.angle_delta(controller_angle, target_angle)
        sector = self._select_movement_sector(relative)
        error = abs(cal_utils.angle_delta((controller_angle + sector * 45) % 360, target_angle))
        if self._directional_cruise_index != self.waypoint_index:
            self._directional_cruise_index = self.waypoint_index
            self._directional_cruise_enabled = error <= NAV_TURN_DEADBAND
            log.debug('贝果侧移起步：允许=%s，按键方向误差=%.1f',
                      self._directional_cruise_enabled, error)
            if not self._directional_cruise_enabled:
                self.heading_aligned = False
        if not self._directional_cruise_enabled:
            return False
        cross_track = 0.0
        if self._waypoint_origin is not None and self.last_position is not None:
            target = self.active_waypoints[self.waypoint_index][1]
            vx = target[0] - self._waypoint_origin[0]
            vy = target[1] - self._waypoint_origin[1]
            length = hypot(vx, vy)
            if length > 0.1:
                cross_track = abs(
                    (self.last_position[0] - self._waypoint_origin[0]) * vy
                    - (self.last_position[1] - self._waypoint_origin[1]) * vx
                ) / length
        if error > NAV_DIRECTION_TURN_LIMIT or cross_track > NAV_DIRECTION_MAX_CROSS_TRACK:
            log.info('贝果本段改用前进对齐：方向误差=%.1f，路段横向偏差=%.2f', error, cross_track)
            self._directional_cruise_enabled = False
            self.heading_aligned = False
        return self._directional_cruise_enabled

    def _cruise_toward(
        self, image_angle: float, target_angle: float, name: str,
    ) -> OperationRoundResult:
        """仅在已对准且未偏离路段时侧移，其余情况使用原前进对齐。"""
        if not self.movement.supports_directions or not self._directional_cruise_allowed(
            (-image_angle) % 360, target_angle,
        ):
            return self._cruise_forward_toward(image_angle, target_angle, name)
        controller_angle, angle_diff = self._observe_heading(image_angle, target_angle)
        sector = self._select_movement_sector(angle_diff)
        movement_error = cal_utils.angle_delta((controller_angle + sector * 45) % 360, target_angle)
        self.movement.hold(sector)
        self.heading_aligned = True
        if self._cruise_progress is None and self.last_position is not None:
            self._cruise_progress = (self.last_position, self.last_screenshot_time)
        if self._ignoring_pickup():
            return self.round_wait('忽略路上可拾取物，继续朝路点移动')
        if abs(movement_error) > NAV_TURN_DEADBAND and (
            self.last_cruise_turn_at is None
            or self.last_screenshot_time - self.last_cruise_turn_at >= NAV_CRUISE_TURN_GAP
        ):
            limited = self._fail_if_action_limit()
            if limited is not None:
                return limited
            self.last_cruise_turn_at = self.last_screenshot_time
            effective = self.turn_compensator.turn(movement_error, max_abs_angle_diff=NAV_CRUISE_TURN_CAP)
            self._record_turn(controller_angle, effective)
            return self.round_wait(f'行进方向微调 {movement_error:.1f}度', wait=NAV_CAMERA_TURN_WAIT)
        return self.round_wait(f'前往{name}')

    def _cruise_forward_toward(
        self, image_angle: float, target_angle: float, name: str,
    ) -> OperationRoundResult:
        """不支持组合移动的后端先停车对齐，再用原前进与镜头微调。"""
        controller_angle, angle_diff = self._observe_heading(image_angle, target_angle)
        if not self.heading_aligned:
            self._release_forward()
            if abs(angle_diff) > NAV_TURN_DEADBAND:
                limited = self._fail_if_action_limit()
                if limited is not None:
                    return limited
                return self._stop_and_turn(controller_angle, angle_diff)
            self.heading_aligned = True
        if abs(angle_diff) > NAV_CRUISE_TURN_LIMIT:
            self._cruise_progress = None
            limited = self._fail_if_action_limit()
            if limited is not None:
                return limited
            return self._stop_and_turn(controller_angle, angle_diff)
        if self._ignoring_pickup():
            # 路边药粉等可拾取物会把镜头拉偏；不按 F，也不跟着小角度来回转。
            if self._cruise_progress is None and self.last_position is not None:
                self._cruise_progress = (self.last_position, self.last_screenshot_time)
            self.movement.hold(0)
            return self.round_wait('忽略路上可拾取物')
        if abs(angle_diff) > NAV_TURN_DEADBAND:
            if (
                self.last_cruise_turn_at is not None
                and self.last_screenshot_time - self.last_cruise_turn_at < NAV_CRUISE_TURN_GAP
            ):
                if self._cruise_progress is None and self.last_position is not None:
                    self._cruise_progress = (self.last_position, self.last_screenshot_time)
                self.movement.hold(0)
                return self.round_wait(f'前往{name}')
            limited = self._fail_if_action_limit()
            if limited is not None:
                return limited
            self.last_cruise_turn_at = self.last_screenshot_time
            if self._cruise_progress is None and self.last_position is not None:
                self._cruise_progress = (self.last_position, self.last_screenshot_time)
            self.movement.hold(0)
            effective = self.turn_compensator.turn(angle_diff, max_abs_angle_diff=NAV_CRUISE_TURN_CAP)
            self._record_turn(controller_angle, effective)
            return self.round_wait(f'行进转向 {angle_diff:.1f}度', wait=NAV_CAMERA_TURN_WAIT)
        if self._cruise_progress is None and self.last_position is not None:
            self._cruise_progress = (self.last_position, self.last_screenshot_time)
        self.movement.hold(0)
        if self._maybe_start_sprint(image_angle, target_angle):
            return self.round_wait('长直线路段冲刺起步')
        return self.round_wait(f'前往{name}')

    def _maybe_start_sprint(self, image_angle: float, target_angle: float) -> bool:
        """仅剩余直段足够长、按键方向稳定且已有实际位移时冲刺一次；暂停不清除记录。"""
        if (
            not self.movement.supports_directions or self.movement.sector is None
            or self.waypoint_index in self._sprinted_waypoints
            or self.last_position is None or self._waypoint_origin is None
            or self.pending_turn is not None
        ):
            self._sprint_probe = None
            return False
        target = self.active_waypoints[self.waypoint_index][1]
        distance = hypot(target[0] - self.last_position[0], target[1] - self.last_position[1])
        _, _, cross_track = self._path_headings()
        if (
            distance < NAV_START_SPRINT_MIN_DISTANCE or cross_track > 1.0
            or abs(cal_utils.angle_delta(
                (-image_angle + self.movement.sector * 45) % 360, target_angle,
            )) > NAV_TURN_DEADBAND
        ):
            self._sprint_probe = None
            return False
        previous = self._sprint_probe
        if previous is None or self.last_screenshot_time - previous[1] > 0.5:
            self._sprint_probe = (self.last_position, self.last_screenshot_time)
            return False
        dx, dy = self.last_position[0] - previous[0][0], self.last_position[1] - previous[0][1]
        if hypot(dx, dy) < 2.0:
            return False
        actual_heading = degrees(atan2(-dy, dx)) % 360
        if abs(cal_utils.angle_delta(actual_heading, target_angle)) > NAV_DIRECTION_TURN_LIMIT:
            self._sprint_probe = (self.last_position, self.last_screenshot_time)
            return False
        self._sprinted_waypoints.add(self.waypoint_index)
        self._sprint_probe = None
        started = self.movement.start_sprint(NAV_START_SPRINT_PRESS)
        if started:
            self.turn_compensator.note_motion()
            self._record_input()
            log.info('贝果长直段冲刺：路点=%s，剩余=%.1f，横向偏差=%.2f；本路段仅一次',
                     self.waypoint_index, distance, cross_track)
        return started

    def _align_to_heading(
        self, image_angle: float, target_angle: float, tolerance: float = NAV_TURN_DEADBAND,
        movement_sector: int = 0,
    ) -> OperationRoundResult | None:
        """短步只修正选定按键方向的残差；容器接近仍使用前进键。"""
        self._release_forward()
        controller_angle, _ = self._observe_heading(image_angle, target_angle)
        angle_diff = cal_utils.angle_delta((controller_angle + movement_sector * 45) % 360, target_angle)
        if abs(angle_diff) > tolerance:
            return self._stop_and_turn(controller_angle, angle_diff)
        if abs(angle_diff) > NAV_TURN_DEADBAND:
            log.debug('贝果近点方向：角差=%.1f，允许=%.1f，当前方向可进入原到达范围',
                      angle_diff, tolerance)
        self.heading_aligned = True
        return None

    def _corner_is_behind(
        self,
        position: tuple[float, float],
        target: tuple[float, float],
        distance: float,
        limit: float = 3,
    ) -> bool:
        """已移动且越过本段目标投影时才推进；到目标的距离仍受原范围限制。"""
        if not self.heading_aligned or distance > limit or self._waypoint_origin is None:
            return False
        vx = target[0] - self._waypoint_origin[0]
        vy = target[1] - self._waypoint_origin[1]
        length2 = vx * vx + vy * vy
        if length2 < 0.01:
            return False
        progressed = (
            (position[0] - self._waypoint_origin[0]) * vx
            + (position[1] - self._waypoint_origin[1]) * vy
        )
        return progressed >= length2

    def _stop_and_turn(self, controller_angle: float, angle_diff: float) -> OperationRoundResult:
        """松键后转向，单次不超过停车上限，随后只等镜头反馈。"""
        self._release_forward()
        effective = self.turn_compensator.turn(angle_diff, max_abs_angle_diff=NAV_STOP_TURN_CAP)
        self.heading_aligned = False
        target = self.active_waypoints[self.waypoint_index][1]
        can_learn = (
            self.turn_compensator.can_learn()
            and self.last_position is not None
            and hypot(target[0] - self.last_position[0], target[1] - self.last_position[1]) > NAV_CRUISE_DISTANCE
        )
        self._record_turn(controller_angle, effective, learn=can_learn)
        return self.round_wait(f'停车转向 {angle_diff:.1f}度', wait=NAV_CAMERA_TURN_WAIT)

    def _record_turn(self, source_angle: float, commanded_angle: float, *, learn: bool = False) -> None:
        """记录限幅后送给控制器的命令；它不等于画面中实际转过的角度。"""
        self._record_input()
        assert self.last_input_frame is not None
        self._turn_feedback_after = self.last_input_frame + NAV_CAMERA_TURN_WAIT
        self.pending_turn = _PendingCameraTurn(
            source_angle, commanded_angle, self.last_input_frame, time.monotonic(),
            learn=learn, source_position=self.last_position,
        )
        log.info(
            '贝果转向命令：sent_at=%.6f source_ccw=%.1f command_ccw=%.1f dx=%.1f scale=%.3f background=%s',
            self.last_input_frame, source_angle, commanded_angle,
            self.ctx.controller.game_config.turn_dx * commanded_angle,
            self.turn_compensator.scale, self.ctx.controller.background_mode,
        )

    def _wait_for_camera_heading(self, reason: str) -> OperationRoundResult:
        """镜头未知时松键，限时等待；不回退到角色箭头或上次镜头角度。"""
        self._release_forward()
        self._discard_turn_feedback()
        now = time.monotonic()
        if self._heading_wait_started is None:
            self._heading_wait_started = now
        if now - self._heading_wait_started >= NAV_CAMERA_WAIT_TIMEOUT:
            return self.round_recoverable_fail(f'镜头朝向持续不可用，停止移动：{reason}')
        return self.round_wait(f'镜头朝向暂时不可用，停步等待：{reason}', wait=0.15)

    def _wait_for_turn_feedback(
        self, image_angle: float, target_angle: float, distance: float,
    ) -> OperationRoundResult | None:
        """大角度中间修正可提前继续，最终对齐及标定必须取得稳定新帧。"""
        turn = self.pending_turn
        if turn is not None and time.monotonic() - turn.started_at >= NAV_CAMERA_WAIT_TIMEOUT:
            self._release_forward()
            self._discard_turn_feedback()
            return self.round_recoverable_fail('镜头转向反馈超时，停止移动')
        # 识别中断后即使丢弃了学习样本，也不能提前再次发送转向。
        if self._turn_feedback_after is not None and self.last_screenshot_time < self._turn_feedback_after:
            return self.round_wait('等待镜头转向后的新截图', wait=0.1)
        if turn is None:
            return None
        controller_angle = (-image_angle) % 360
        if self.heading_aligned and self.movement.supports_directions and self._directional_cruise_enabled:
            # 新镜头角足以重新选择移动键；行进镜头输入不作为停车标定样本。
            turn.learn = False
            return None
        if abs(cal_utils.angle_delta(turn.source_angle, controller_angle)) <= 1e-6:
            if self.heading_aligned and self.last_screenshot_time < turn.sent_at + NAV_CAMERA_EFFECT_WAIT:
                return self.round_wait('等待行进转向生效', wait=0.05)
            self._release_forward()
            self.heading_aligned = False
            turn.feedback_angle = None
            turn.feedback_time = None
            return self.round_wait('等待镜头转向生效', wait=NAV_CAMERA_STABLE_INTERVAL)
        if self.heading_aligned:
            return None
        observed = cal_utils.angle_delta(turn.source_angle, controller_angle)
        expected = turn.commanded_angle / self.turn_compensator.scale
        remaining = abs(cal_utils.angle_delta(controller_angle, target_angle))
        if (
            self.turn_compensator.calibrated
            and distance > NAV_CRUISE_DISTANCE
            and (not turn.learn or not self.turn_compensator.needs_sample)
            and remaining > NAV_FAST_TURN_REMAINDER
            and observed * expected > 0
            and abs(observed) >= abs(expected) * 0.85
            and abs(observed - expected) <= NAV_TURN_DEADBAND
            and self._turn_position_stable(turn)
        ):
            turn.learn = False
            log.debug('贝果快速补转：预计=%.1f，观测=%.1f，剩余=%.1f；本次不用于标定',
                      expected, observed, remaining)
            return None
        self._release_forward()
        previous_angle = turn.feedback_angle
        previous_time = turn.feedback_time
        if previous_time is not None and self.last_screenshot_time - previous_time < NAV_CAMERA_STABLE_INTERVAL:
            return self.round_wait('等待下一张镜头反馈截图', wait=NAV_CAMERA_STABLE_INTERVAL)
        turn.feedback_angle = controller_angle
        turn.feedback_time = self.last_screenshot_time
        if (
            previous_angle is None
            or abs(cal_utils.angle_delta(previous_angle, controller_angle)) > NAV_CAMERA_STABLE_DELTA
        ):
            return self.round_wait('等待停车转向后的镜头稳定', wait=NAV_CAMERA_STABLE_INTERVAL)
        return None

    def _turn_position_stable(self, turn: _PendingCameraTurn) -> bool:
        """受击击退或明显位移后，不把镜头变化当作独立停车反馈。"""
        return (
            turn.source_position is not None and self.last_position is not None
            and hypot(
                self.last_position[0] - turn.source_position[0],
                self.last_position[1] - turn.source_position[1],
            ) <= NAV_SAFE_APPROACH_DISTANCE
        )

    def _discard_turn_feedback(self) -> None:
        """画面中断或定位失效后丢弃待确认命令，恢复后重新依据镜头选择移动方向。"""
        self.heading_aligned = False
        self.pending_turn = None
        self.turn_compensator.clear_pending_sample()

    def _observe_heading(self, image_angle: float, target_angle: float) -> tuple[float, float]:
        """只用已通过反馈等待的新镜头观测学习转幅，转换一次控制器角度。"""
        controller_angle = (-image_angle) % 360
        if self.pending_turn is not None:
            turn = self.pending_turn
            if turn.learn and self._turn_position_stable(turn):
                self.turn_compensator.learn(turn.source_angle, turn.commanded_angle, controller_angle)
            self.pending_turn = None
        return controller_angle, cal_utils.angle_delta(controller_angle, target_angle)

    def _ignoring_pickup(self) -> bool:
        """右侧出现 F 提示且不是已支持的容器时，忽略路边可拾取物。"""
        if not self.round_by_find_area(
            self.last_screenshot, '贝果-局内', '交互F键',
        ).is_success:
            return False
        for area_name in CONTAINER_PROMPT_AREAS.values():
            if self.round_by_find_area(
                self.last_screenshot, '贝果-局内', area_name,
            ).is_success:
                return False
        return True

    def _action_limit(self) -> int:
        """独立坐标移动统一限制 100 次；保留旧武备箱整段入口的上限。"""
        return 60 if self.destination == 'box' else 100

    def _fail_if_action_limit(self) -> OperationRoundResult | None:
        """计次输入达到上限时松键停止。按住观察不走这里。"""
        if not self.coordinate_only and self.recovery.error():
            self._release_forward()
            return self.round_recoverable_fail(self.recovery.error())
        if self.steps < self._action_limit():
            return None
        self._release_forward()
        return self.round_recoverable_fail('导航达到动作上限')

    def _release_forward(self) -> None:
        """释放本导航持有的前进、侧移和后退键，所有退出分支共用。"""
        self._cruise_progress = None
        self._sprint_probe = None
        if self.movement.moving:
            self.turn_compensator.note_motion()
        try:
            self.movement.stop()
        finally:
            self.ctx.controller.stop_moving_forward()

    def _record_input(self) -> None:
        """输入动作计数并记录完成时刻；按住观察不计，后续只使用新截图。"""
        self.steps += 1
        self.last_input_frame = max(time.time(), self.last_screenshot_time)

    def _invalidate_heading(self) -> None:
        """启动或暂停后不再信任旧镜头方向和转向样本。"""
        self._discard_turn_feedback()
        self._heading_wait_started = None
        self._turn_feedback_after = None
        self.last_input_frame = None
        self.target_wait_started = None
        self._waypoint_origin = None
        self._waypoint_origin_index = -1
        self._directional_cruise_index = -1
        self._directional_cruise_enabled = False

    def handle_pause(self) -> None:
        """暂停时释放所有移动键，恢复后重新观测方向。"""
        self._release_forward()
        self._settle_until = None
        self._settled = False
        self._invalidate_heading()
        self.turn_compensator.clear_pending_sample()
        super().handle_pause()

    def after_operation_done(self, result: OperationResult) -> None:
        """所有退出路径释放移动键并保留失败截图。"""

        self._release_forward()
        self._invalidate_heading()
        super().after_operation_done(result)
