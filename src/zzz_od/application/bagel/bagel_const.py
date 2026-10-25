APP_ID: str = 'bagel'
APP_NAME: str = '贝果计划'
DEFAULT_GROUP: bool = True
# 排在驱动盘拆解之后、通知之前。数值越小越靠前。
PRIORITY: int = 1800
NEED_NOTIFY: bool = False
MAP_TITLE: str = '高危雅努斯幻境'
RECOMMENDED_VALUE: str = '258000'

# 容器类型同时用于流程校验、导航、面板识别和交互。
CONTAINER_LABELS: dict[str, str] = {
    'box': '武备箱', 'mech': '机械保险箱', 'safe': '电子保险箱',
}
CONTAINER_TITLE_AREAS: dict[str, str] = {
    'box': '搜查容器标题', 'mech': '机械保险箱标题', 'safe': '电子保险箱标题',
}
CONTAINER_PROMPT_AREAS: dict[str, str] = {
    'box': '武备箱交互', 'mech': '机械保险箱交互', 'safe': '电子保险箱交互',
}
CONTAINER_ARRIVE_STATUS: dict[str, str] = {
    'box': '已到武备箱', 'mech': '已到机械保险箱', 'safe': '已到电子保险箱',
}
MECH_HOLD_PRESS_TIME: float = 3.5

# 距节点超过此像素按住前进；以内改为短步。电子保险箱最后一段始终碎步。
NAV_CRUISE_DISTANCE: float = 10
# 电子保险箱巷口拐点在这个距离内就切下一段。切晚了会斜穿停车位；切得太早会在巷口原地大转弯，小地图对不上。
NAV_SAFE_CORNER_DISTANCE: float = 12
# 楼角和贴墙中间路点的切段距离。
NAV_SAFE_APPROACH_DISTANCE: float = 2
# 最后接近点前提前松键，给截图延迟与停步前移留出余量；横向仍须贴墙。
NAV_SAFE_BRAKE_DISTANCE: float = 6
NAV_SAFE_BRAKE_WAIT: float = 0.5
# 朝向偏差（度）：以内不转；不超过停车阈值时边走边转；再大则松键停车转。
# 普通路点近处按到达圆判定可前进方向，最多允许三十度偏差。
NAV_NEAR_TURN_LIMIT: float = 30
NAV_TURN_DEADBAND: float = 7
NAV_CRUISE_TURN_LIMIT: float = 45
NAV_CRUISE_TURN_CAP: float = 30
# 边走边转的最小间隔（秒）。间隔内只按住前进，避免转向还没落地又往反方向补。
NAV_CRUISE_TURN_GAP: float = 0.4
# 超过此横向偏差时收键；普通路点按新定位短步返回路段，容器接近仍退回前进。
NAV_DIRECTION_MAX_CROSS_TRACK: float = 2.0
NAV_DIRECTION_TURN_LIMIT: float = 12.0
# 普通路段的前视距离；目标沿当前路段推进，及时抵消八方向的横向误差。
NAV_PATH_LOOKAHEAD: float = 4.0
# 仅剩余直段至少10px、已经沿路段起步时触发一次；不按整条流程总长度判断。
NAV_START_SPRINT_MIN_DISTANCE: float = 10.0
NAV_START_SPRINT_PRESS: float = 0.08
NAV_SPRINT_BRAKE_WAIT: float = 0.15
# 不停步路点的角前短步只减速；到点后仍在同一帧接续下一段。
NAV_PASS_FORWARD_PRESS: float = 0.12
NAV_PASS_FRAME_WAIT: float = 0.05
NAV_STOP_TURN_CAP: float = 90
# 转向后先等新帧；最终对齐和标定仍须连续两帧稳定。
NAV_CAMERA_TURN_WAIT: float = 0.12
# 行进中首帧尚未体现转向时，保留原有三百毫秒响应窗口。
NAV_CAMERA_EFFECT_WAIT: float = 0.3
# 已标定的大角度修正允许使用已接近预计转幅的新观测继续转。
NAV_FAST_TURN_REMAINDER: float = 45
# 停车反馈须连续两帧接近；不复用目标对齐的七度死区。
NAV_CAMERA_STABLE_DELTA: float = 2
NAV_CAMERA_STABLE_INTERVAL: float = 0.08
# 镜头持续未知、转向没有响应或停车转向未稳定时的等待上限（秒）。
NAV_CAMERA_WAIT_TIMEOUT: float = 2.0
# 普通短步、电子保险箱最后碎步的按下时长（秒）。
NAV_FORWARD_PRESS: float = 0.2
NAV_SAFE_APPROACH_PRESS: float = 0.08
# 首次导航位置尚未取得时，只对几何证据不足松键等待。
NAV_INITIAL_LOCATE_MISS_LIMIT: int = 5
NAV_INITIAL_LOCATE_WAIT: float = 0.3
# 已经定位成功后，允许连续几帧对不上再停。单帧遮挡不再结束本段。
NAV_LOCATE_MISS_LIMIT: int = 3

# 电子保险箱完成四轮点按后等待搜索结果，点按时机按光圈状态判断。
SAFE_UNLOCK_HITS: int = 4

# 仓库清理：default 按下面的两步出售；custom 按 clean_steps 顺序出售。
CLEAN_MODE_DEFAULT: str = 'default'
CLEAN_MODE_CUSTOM: str = 'custom'
CLEAN_TYPES: tuple[str, ...] = ('贵重物品', '战术棱镜', '其他', '装备', '战术道具', '门禁卡')
CLEAN_QUALITIES: tuple[str, ...] = ('C', 'B', 'A', 'S', 'Z')
# 每步分别保存类型和品质，不能合并成一组，否则会卖掉 S 品质其他物品。
DEFAULT_CLEAN_STEPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (('贵重物品', '战术棱镜'), ('C', 'B', 'A', 'S')),
    (('其他',), ('C', 'B', 'A')),
)
DEFAULT_CLEAN_FILTER_GROUPS: tuple[tuple[str, ...], ...] = tuple(
    tuple(f'筛选-{name}' for name in (*types, *qualities))
    for types, qualities in DEFAULT_CLEAN_STEPS
)
