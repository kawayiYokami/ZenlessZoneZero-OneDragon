import cv2
from cv2.typing import MatLike

from one_dragon.utils import cv2_utils, mini_map_angle_utils
from zzz_od.context.zzz_context import ZContext
from zzz_od.game_map.mini_map_wrapper import TOTAL_VIEW_ANGLE, RADIUS_RANGE, MiniMapWrapper


class ZzzMapService:

    def __init__(
            self,
            ctx: ZContext,
    ):
        self.ctx: ZContext = ctx

    def cut_mini_map(self, screen: MatLike) -> MiniMapWrapper:
        """
        截取小地图

        Args:
            screen: 游戏画面

        Returns:
            MatLike: 小地图图片
        """
        area = self.ctx.screen_loader.get_area('大世界', '小地图')
        rgb = cv2_utils.crop_image_only(screen, area.rect)
        return MiniMapWrapper(rgb)


def __debug_total_angle(debug_image_name: str):
    ctx = ZContext()

    from one_dragon.utils import debug_utils
    screen = debug_utils.get_debug_image(debug_image_name)
    mini_map = ctx.mini_map_service.cut_mini_map(screen)
    mask = mini_map.view_mask
    _, results = mini_map_angle_utils.calculate_sector_angle(mask, radius_range=RADIUS_RANGE,
                                                             debug_steps=True)
    from one_dragon.utils.mini_map_angle_visualizer import visualize_calculate_sector_angle
    visualize_calculate_sector_angle(mini_map, results)


def __debug_cal_angle(debug_image_name: str, save: bool = False):
    ctx = ZContext()

    from one_dragon.utils import debug_utils
    screen = debug_utils.get_debug_image(debug_image_name)
    mini_map = ctx.mini_map_service.cut_mini_map(screen)

    mask = mini_map.view_mask
    _, results = mini_map_angle_utils.calculate(mask, view_angle=TOTAL_VIEW_ANGLE, radius_range=RADIUS_RANGE, debug_steps=True)
    # from one_dragon.utils.mini_map_angle_visualizer import visualize_calculate
    # visualize_calculate(mini_map.rgb, results)

    if save:
        from one_dragon.utils import os_utils
        save_dir = os_utils.get_path_under_work_dir('zzz-od-test', 'test', 'zzz_od', 'context', 'test_mini_map_service', 'test_cal_angle')
        int_angle = int(results['view_angle'])
        idx: int = 1
        while True:
            file_name: str = f'{int_angle}_{idx}.png'
            import os
            file_path: str = os.path.join(save_dir, file_name)
            if os.path.exists(file_path):
                idx += 1
            else:
                break
        cv2_utils.save_image(mini_map.rgb, file_path)


def __debug_road_mask(debug_image_name: str):
    ctx = ZContext()
    from one_dragon.utils import debug_utils
    screen = debug_utils.get_debug_image(debug_image_name)
    mini_map = ctx.mini_map_service.cut_mini_map(screen)
    import time
    start_time = time.time()
    # CLAHE 可以让道路更有辨识度 但背景亮时 会让灰色带变得更亮 颜色偏差很大 很难选择
    # clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(3, 3))
    # y = clahe.apply(mini_map.yuv_y)
    # u = mini_map.yuv_u
    # v = mini_map.yuv_v
    # rgb = cv2.cvtColor(cv2.merge([y, u, v]), cv2.COLOR_YUV2RGB)
    # cv2_utils.show_image(rgb, win_name='rgb')
    mask = mini_map.road_mask
    print(time.time() - start_time)
    cv2_utils.show_image(mini_map.rgb, win_name='mini_map')
    cv2_utils.show_image(mask, win_name='mask', wait=0)
    cv2.destroyAllWindows()


if __name__ == '__main__':
    # __debug_total_angle('_1752932914363')
    __debug_cal_angle('_1753628033007', save=True)
    # __debug_road_mask('_1753019229412')
