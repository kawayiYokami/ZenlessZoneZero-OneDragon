import cv2
import json
import os
import time
import shutil
from typing import Optional
from one_dragon.utils.log_utils import log

from cv2.typing import MatLike

from one_dragon.base.geometry.point import Point
from one_dragon.base.operation.application import application_const
from one_dragon.base.operation.operation_base import OperationResult
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils import cv2_utils, os_utils
from zzz_od.application.drive_disk_scan import drive_disk_scan_const
from zzz_od.application.drive_disk_scan.drive_disk_scan_config import DriveDiskScanConfig
from zzz_od.application.drive_disk_scan.drive_disk_parser import DriveDiskParser
from zzz_od.application.zzz_application import ZApplication
from zzz_od.context.zzz_context import ZContext


class DriveDiskScanApp(ZApplication):

    def __init__(self, ctx: ZContext):
        ZApplication.__init__(
            self,
            ctx=ctx,
            app_id=drive_disk_scan_const.APP_ID,
            op_name=drive_disk_scan_const.APP_NAME,
            node_max_retry_times=100000,
        )
        self.config: DriveDiskScanConfig = self.ctx.run_context.get_config(
            app_id=drive_disk_scan_const.APP_ID,
            instance_idx=self.ctx.current_instance_idx,
            group_id=application_const.DEFAULT_GROUP_ID,
        )

        # 当前位置（行，列）从(0,0)开始，对应界面上的(1,1)
        self.current_row_idx: int = 0
        self.current_col_idx: int = 0
        
        # 网格信息（每次换行时更新）
        self.grid_rows: list[list[Point]] = []  # 二维网格
        self.total_scanned: int = 0  # 已扫描总数
        
        # 截图临时文件夹和序号
        self.screenshots_dir: Optional[str] = None
        self.screenshot_index: int = 0  # 全局递增序号
        
        # OCR处理标记
        self.ocr_processed: bool = False  # 防止重复OCR
        
        # 驱动盘属性解析器
        self.parser = DriveDiskParser()
        # 已扫描的驱动盘数据列表
        self.scanned_discs: list[dict] = []
        # 本次扫描的导出文件路径
        self.export_path: Optional[str] = None

    def execute(self) -> OperationResult:
        """执行扫描"""
        # 准备截图文件夹
        self._prepare_screenshots_dir()
        
        try:
            result = super().execute()
            
            # 扫描完成后批量OCR
            if '扫描完成' in result.status:
                log.info("开始批量OCR处理...")
                self._batch_process_screenshots()
            
            return result
        finally:
            pass

    def _prepare_screenshots_dir(self):
        """准备截图临时文件夹"""
        base_dir = os_utils.get_path_under_work_dir('.debug', 'drive_disk_screenshots')
        
        # 如果文件夹存在，清空
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)
        
        os.makedirs(base_dir, exist_ok=True)
        self.screenshots_dir = base_dir
        log.info(f"截图文件夹已准备: {self.screenshots_dir}")

    def _save_screenshot(self, row: int, col: int, screenshot: MatLike):
        """保存截图到文件"""
        if self.screenshots_dir is None:
            return
        
        # 文件名格式: index_row_col.jpg (例如: 0000_0_0.jpg表示序号0，第1行第1列)
        filename = f"{self.screenshot_index:04d}_{row}_{col}.jpg"
        filepath = os.path.join(self.screenshots_dir, filename)
        
        try:
            # 截图是RGB，转换为BGR后保存（OpenCV需要BGR格式）
            screenshot_bgr = cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR)
            cv2.imwrite(filepath, screenshot_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            self.screenshot_index += 1
        except Exception as e:
            log.error(f"保存截图失败({row+1},{col+1}): {e}")

    def _batch_process_screenshots(self):
        """批量处理所有截图进行OCR"""
        # 防止重复执行
        if self.ocr_processed:
            log.info("OCR已处理完成，跳过重复执行")
            return
        
        self.ocr_processed = True
        
        if self.screenshots_dir is None or not os.path.exists(self.screenshots_dir):
            log.error("截图文件夹不存在")
            return
        
        # 获取所有截图文件并按序号排序
        files = [f for f in os.listdir(self.screenshots_dir) if f.endswith('.jpg')]
        files.sort()  # 按文件名排序（序号在前，自然排序）
        
        total_files = len(files)
        log.info(f"找到{total_files}个截图文件，开始OCR处理...")
        
        for idx, filename in enumerate(files, 1):
            try:
                # 解析文件名: index_row_col.jpg
                parts = filename[:-4].split('_')
                index = int(parts[0])
                row = int(parts[1])
                col = int(parts[2])
                
                # 读取截图
                filepath = os.path.join(self.screenshots_dir, filename)
                screenshot = cv2.imread(filepath)
                
                if screenshot is None:
                    log.error(f"读取截图失败: {filename}")
                    continue
                
                # OCR处理
                self._process_screenshot_ocr(row, col, screenshot)
                
                # 进度日志（每10个输出一次）
                if idx % 10 == 0 or idx == total_files:
                    log.info(f"OCR进度: {idx}/{total_files} ({idx*100//total_files}%)")
                
            except Exception as e:
                log.error(f"处理截图异常 {filename}: {e}")
        
        log.info(f"OCR处理完成，共识别{len(self.scanned_discs)}个驱动盘")

    def _process_screenshot_ocr(self, row: int, col: int, screenshot: MatLike):
        """处理单个截图的OCR"""
        try:
            ctx = self.ctx.cv_service.run_pipeline('驱动盘属性识别', screenshot)
            if ctx.error_str is not None:
                log.warning(f"OCR失败({row+1},{col+1}): {ctx.error_str}")
                return
            
            if ctx.ocr_result is None:
                log.warning(f"OCR结果为空({row+1},{col+1})")
                return
            
            # 转换OCR结果
            ocr_items = []
            if isinstance(ctx.ocr_result, dict):
                for text in ctx.ocr_result.keys():
                    ocr_items.append({'text': text, 'confidence': 1.0, 'position': None})
            elif isinstance(ctx.ocr_result, list):
                for item in ctx.ocr_result:
                    if isinstance(item, str):
                        ocr_items.append({'text': item, 'confidence': 1.0, 'position': None})
                    elif isinstance(item, dict):
                        ocr_items.append(item)
                    else:
                        box = item.box if hasattr(item, 'box') else None
                        if box and len(box) >= 4:
                            x1, y1 = min(p[0] for p in box), min(p[1] for p in box)
                            x2, y2 = max(p[0] for p in box), max(p[1] for p in box)
                            position = (x1, y1, x2, y2)
                        else:
                            position = None
                        ocr_items.append({
                            'text': item.text if hasattr(item, 'text') else str(item),
                            'confidence': item.score if hasattr(item, 'score') else 1.0,
                            'position': position
                        })
            
            # 解析并保存
            disc_data = self.parser.parse_ocr_result(ocr_items)
            if disc_data:
                self.scanned_discs.append(disc_data)
                log.info(f"识别成功({row+1},{col+1}): {disc_data.get('setKey', 'Unknown')} [{disc_data.get('slotKey', '?')}] Lv.{disc_data.get('level', 0)}")
            else:
                log.warning(f"解析失败({row+1},{col+1})")
                
        except Exception as e:
            log.error(f"OCR异常({row+1},{col+1}): {e}")

    @operation_node(name='初始化对齐', is_start_node=True)
    def initialize_align(self) -> OperationRoundResult:
        """开始前点击(1,1)确保坐标对齐"""
        screen = self.last_screenshot
        
        ctx = self.ctx.cv_service.run_pipeline('驱动盘方格', screen)
        if ctx.error_str is not None:
            return self.round_success(f'检测失败:{ctx.error_str}')
        
        if ctx.contours is None or len(ctx.contours) == 0:
            return self.round_success('未检测到方格')
        
        # 获取所有方格
        absolute_rects = ctx.get_absolute_rects()
        all_grids = []
        for x1, y1, x2, y2 in absolute_rects:
            center = Point(x1 + (x2 - x1) / 2, y1 + (y2 - y1) / 2)
            all_grids.append(center)
        
        # 网格化排序
        self.grid_rows = self._sort_grids(all_grids)
        
        if not self.grid_rows or len(self.grid_rows[0]) == 0:
            return self.round_success('网格为空')
        
        # 点击(1,1)确保对齐
        target = self.grid_rows[0][0]
        self.ctx.controller.click(target)
        
        return self.round_success(f'已对齐到(1,1) 检测到{len(self.grid_rows)}行', wait=0.3)

    @node_from(from_name='初始化对齐')
    @node_from(from_name='扫描并点击下一格', status='换行')
    @operation_node(name='检测网格')
    def detect_grid(self) -> OperationRoundResult:
        """检测当前可见的驱动盘网格"""
        screen = self.last_screenshot
        
        ctx = self.ctx.cv_service.run_pipeline('驱动盘方格', screen)
        if ctx.error_str is not None:
            return self.round_success(f'检测失败:{ctx.error_str}')
        
        if ctx.contours is None or len(ctx.contours) == 0:
            return self.round_success('未检测到方格')
        
        # 获取所有方格的绝对坐标中心点
        absolute_rects = ctx.get_absolute_rects()
        all_grids = []
        for x1, y1, x2, y2 in absolute_rects:
            center = Point(x1 + (x2 - x1) / 2, y1 + (y2 - y1) / 2)
            all_grids.append(center)
        
        # 网格化排序
        self.grid_rows = self._sort_grids(all_grids)
        
        if not self.grid_rows:
            return self.round_success('网格为空')
        
        return self.round_success(f'检测到{len(self.grid_rows)}行 第1行{len(self.grid_rows[0])}列')

    @node_from(from_name='检测网格')
    @node_from(from_name='扫描并点击下一格')  # 自循环：同行连续点击
    @operation_node(name='扫描并点击下一格')
    def scan_and_click_next(self) -> OperationRoundResult:
        """扫描当前格并点击下一格"""
        if not self.grid_rows:
            return self.round_fail('网格未初始化')
        
        # 检查是否超出范围
        if self.current_row_idx >= len(self.grid_rows):
            # 扫描完成，开始OCR
            log.info("点击完成，开始批量OCR处理...")
            self._batch_process_screenshots()
            return self.round_fail('扫描完成')
        
        current_row = self.grid_rows[self.current_row_idx]
        if self.current_col_idx >= len(current_row):
            # 当前行结束，开始OCR
            log.info("点击完成，开始批量OCR处理...")
            self._batch_process_screenshots()
            return self.round_fail('扫描完成')
        
        # 保存当前截图到文件
        current_screenshot = self.last_screenshot.copy()
        self._save_screenshot(self.current_row_idx, self.current_col_idx, current_screenshot)
        
        # 计算下一个位置
        next_row = self.current_row_idx
        next_col = self.current_col_idx + 1
        
        # 检查是否需要换行
        if next_col >= len(current_row):
            next_row += 1
            next_col = 0
            
            # 第4行第1个(索引3,0)：需要特殊处理
            if next_row == 3 and next_col == 0:
                # 点击(3,2)前检测进度条，判断是否已是最后一行
                ctx = self.ctx.cv_service.run_pipeline('驱动盘进度条检测', self.last_screenshot)
                has_progress_bar = ctx.contours is not None and len(ctx.contours) > 0
                
                if has_progress_bar:
                    # 有进度条：第3行就是最后一行，直接从(4,2)开始
                    if len(self.grid_rows) > 3 and len(self.grid_rows[3]) > 1:
                        target = self.grid_rows[3][1]  # 点击(4,2)
                        self.ctx.controller.click(target)
                        self.current_row_idx = 3  # 第4行（索引3）
                        self.current_col_idx = 1  # 第2列（索引1）
                        self.total_scanned += 1
                        return self.round_success('最后一行 从(4,2)开始')
                    else:
                        # 扫描完成，开始OCR
                        log.info("点击完成，开始批量OCR处理...")
                        self._batch_process_screenshots()
                        return self.round_fail('扫描完成')
                else:
                    # 无进度条：点击(4,1)触发滚动
                    if len(self.grid_rows) > 3 and len(self.grid_rows[3]) > 0:
                        target = self.grid_rows[3][0]
                        self.ctx.controller.click(target)
                        self.total_scanned += 1
                        # 设置为(3,1)，下次循环会+1变成(3,2)
                        self.current_row_idx = 2  # 第3行（索引2）
                        self.current_col_idx = 0  # 第1列（索引0）
                        return self.round_success('滚动', wait=0.5)  # 等待滚动
                    else:
                        # 扫描完成，开始OCR
                        log.info("点击完成，开始批量OCR处理...")
                        self._batch_process_screenshots()
                        return self.round_fail('扫描完成')
            
            # 普通换行（不等待）
            if next_row < len(self.grid_rows):
                if len(self.grid_rows[next_row]) > 0:
                    target = self.grid_rows[next_row][0]
                    self.ctx.controller.click(target)
                    self.current_row_idx = next_row
                    self.current_col_idx = next_col
                    self.total_scanned += 1
                    return self.round_success('换行')  # 重新检测网格
            else:
                # 扫描完成，开始OCR
                log.info("点击完成，开始批量OCR处理...")
                self._batch_process_screenshots()
                return self.round_fail('扫描完成')
        
        # 同行点击下一个（不需要额外等待）
        target = current_row[next_col]
        self.ctx.controller.click(target)
        self.current_row_idx = next_row
        self.current_col_idx = next_col
        self.total_scanned += 1
        
        return self.round_success(f'({self.current_row_idx+1},{self.current_col_idx+1}) 已扫{self.total_scanned}')

    def _sort_grids(self, all_disks: list[Point]) -> list[list[Point]]:
        """将所有方格整理为二维网格"""
        if not all_disks:
            return []

        # 按 y 坐标排序
        sorted_by_y = sorted(all_disks, key=lambda d: d.y)

        rows = []
        current_row = [sorted_by_y[0]]
        y_tolerance = 50

        for i in range(1, len(sorted_by_y)):
            disk = sorted_by_y[i]
            if abs(disk.y - current_row[0].y) <= y_tolerance:
                current_row.append(disk)
            else:
                current_row.sort(key=lambda d: d.x)
                rows.append(current_row)
                current_row = [disk]

        if current_row:
            current_row.sort(key=lambda d: d.x)
            rows.append(current_row)

        return rows

    def on_pause(self):
        """暂停"""
        super().on_pause()

    def on_resume(self):
        """恢复"""
        super().on_resume()

    def after_operation_done(self, result: OperationResult):
        """扫描完成后的清理工作"""
        # 最终导出
        if self.scanned_discs:
            self._export_scanned_discs()
            log.info(f"扫描完成，共识别{len(self.scanned_discs)}个驱动盘")
        
        ZApplication.after_operation_done(self, result)
    
    def _export_scanned_discs(self):
        """导出已扫描的驱动盘数据为JSON文件"""
        try:
            # 第一次导出时创建文件路径
            if self.export_path is None:
                export_dir = os_utils.get_path_under_work_dir('.debug', 'drive_disk_exports')
                os.makedirs(export_dir, exist_ok=True)
                
                timestamp = int(time.time())
                self.export_path = os.path.join(export_dir, f'drive_disks_{timestamp}.json')
            
            # 生成JSON
            json_str = self.parser.generate_export_json(self.scanned_discs)
            
            # 保存到文件
            with open(self.export_path, 'w', encoding='utf-8') as f:
                f.write(json_str)
            
            log.info(f'已导出{len(self.scanned_discs)}个驱动盘数据到: {self.export_path}')
            
        except Exception as e:
            log.error(f'导出驱动盘数据失败: {e}')


def __debug():
    ctx = ZContext()
    ctx.init()
    ctx.run_context.start_running()
    app = DriveDiskScanApp(ctx)
    app.execute()


if __name__ == '__main__':
    __debug()