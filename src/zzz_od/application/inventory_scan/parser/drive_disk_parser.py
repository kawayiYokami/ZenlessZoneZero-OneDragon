import json
import re
from typing import Optional, Dict, List, Any


class DriveDiskParser:
    """驱动盘属性解析器，根据OCR结果生成JSON数据"""

    # 主属性映射（主属性都是百分比，需要加下划线后缀）
    MAIN_STAT_MAP = {
        # 英文
        'HP': 'hp_',
        'ATK': 'atk_',
        'DEF': 'def_',
        'CRIT Rate': 'crit_',
        'CRIT DMG': 'crit_dmg_',
        'Anomaly Proficiency': 'anomProf',
        'PEN Ratio': 'pen_',
        'Impact': 'impact',
        'Energy Regen': 'energyRegen_',
        'Energy': 'energyRegen_',
        'Regen': 'energyRegen_',
        # 中文
        '生命值': 'hp_',
        '攻击力': 'atk_',
        '防御力': 'def_',
        '暴击率': 'crit_',
        '暴击伤害': 'crit_dmg_',
        '异常精通': 'anomProf',
        '穿透率': 'pen_',
        '冲击力': 'impact',
        '能量自动回复': 'energyRegen_',
    }

    # 副属性映射
    SUB_STAT_MAP = {
        # 英文
        'HP': 'hp',
        'ATK': 'atk',
        'ATK.': 'atk',
        'DEF': 'def',
        'CRIT Rate': 'crit_',
        'CRIT DMG': 'crit_dmg_',
        'PEN Ratio': 'pen_',
        'PEN': 'pen',
        'Anomaly Proficiency': 'anomProf',
        'Impact': 'impact',
        # 中文
        '生命值': 'hp',
        '攻击力': 'atk',
        '防御力': 'def',
        '暴击率': 'crit_',
        '暴击伤害': 'crit_dmg_',
        '穿透率': 'pen_',
        '穿透值': 'pen',
        '异常精通': 'anomProf',
        '冲击力': 'impact',
    }

    def __init__(self):
        self.disc_counter = 0
        # 初始化翻译服务
        from zzz_od.application.inventory_scan.translation import TranslationService
        self.translation_service = TranslationService()

    def parse_ocr_result(self, ocr_texts: List[Dict[str, Any]]) -> Optional[Dict]:
        """
        解析OCR结果生成驱动盘JSON数据

        Args:
            ocr_texts: OCR识别结果列表，每项包含 text, confidence, position 等信息
                      position格式: (x1, y1, x2, y2)

        Returns:
            驱动盘JSON数据字典，解析失败返回None
        """
        try:
            # 保留完整的OCR项（包含位置信息）
            ocr_items = []
            texts = []
            for item in ocr_texts:
                if isinstance(item, dict):
                    text = item.get('text', '')
                    ocr_items.append(item)
                else:
                    text = str(item)
                    ocr_items.append({'text': text})
                texts.append(text)

            # 解析套装名称（通常在前两个文本中）
            set_key = self._parse_set_name(texts)

            # 解析位置（[1] [2] [3] [4] [5] [6]）
            slot_key = self._parse_slot(texts)

            # 解析等级（Lv. 15/15）
            level = self._parse_level(texts)

            # 解析主属性（根据位置判断是否百分比）
            main_stat_key, main_stat_value = self._parse_main_stat(texts, slot_key)

            # 解析副属性（需要位置信息来匹配升级标记，且需要排除主属性）
            substats = self._parse_substats(ocr_items, main_stat_key)

            # 生成驱动盘数据
            self.disc_counter += 1
            disc_data = {
                'setKey': set_key,
                'rarity': 'S',  # 默认S级，可以根据RARITY字段判断
                'level': level,
                'slotKey': slot_key,
                'mainStatKey': main_stat_key,
                'substats': substats,
                'location': '',
                'lock': False,
                'trash': False,
                'id': f'zzz_disc_{self.disc_counter}'
            }

            return disc_data

        except Exception as e:
            print(f"解析OCR结果失败: {e}")
            return None

    def _parse_set_name(self, texts: List[str]) -> str:
        """解析套装名称"""
        # 找到包含[数字]的文本，提取套装名称
        set_name = None

        for text in texts:
            # 检查是否包含[数字]
            if re.search(r'\[\d\]', text):
                # 找到了[数字]，提取[数字]前的文字
                match = re.match(r'(.+?)\s*\[\d\]', text)
                if match:
                    set_name = match.group(1).strip()
                break

        if not set_name:
            set_name = texts[0] if texts else 'Unknown'

        # 使用translation服务翻译成英文
        en_name = self.translation_service.translate_equipment(set_name, 'EN')

        # 转换成驼峰命名key（移除空格）
        set_key = en_name.replace(' ', '')

        return set_key

    def _parse_slot(self, texts: List[str]) -> str:
        """解析装备位置"""
        for text in texts:
            match = re.search(r'\[(\d)\]', text)
            if match:
                return match.group(1)
            # 单独的数字1-6
            if text.strip() in ['1', '2', '3', '4', '5', '6']:
                return text.strip()
        return '1'

    def _parse_level(self, texts: List[str]) -> int:
        """解析等级"""
        for text in texts:
            # 匹配中文格式: 等级15/15
            match = re.search(r'等级\s*(\d+)', text)
            if match:
                return int(match.group(1))
            # 匹配英文格式: Lv. 15/15 或 Lv.15
            match = re.search(r'Lv\.?\s*(\d+)', text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 0

    def _parse_main_stat(self, texts: List[str], slot_key: str) -> tuple[str, Optional[float]]:
        """
        解析主属性
        位置1-3：固定值（无下划线）- HP, ATK, DEF
        位置4-6：百分比（有下划线）- HP%, ATK%, DEF%, CRIT Rate%, etc.
        """
        main_stat_key = None
        main_stat_value = None

        # 找到 "Main Stat" 或 "主属性" 关键字
        main_stat_idx = -1
        for i, text in enumerate(texts):
            if 'Main Stat' in text or 'Main' in text or '主属性' in text:
                main_stat_idx = i
                break

        if main_stat_idx >= 0:
            # 主属性名称通常在下一行
            for i in range(main_stat_idx + 1, min(main_stat_idx + 3, len(texts))):
                for key in self.MAIN_STAT_MAP.keys():
                    if key in texts[i]:
                        # 根据位置判断是否百分比
                        value = self.MAIN_STAT_MAP[key]
                        if slot_key in ['1', '2', '3']:
                            # 位置1-3：固定值，移除下划线
                            # hp_ → hp, atk_ → atk, def_ → def
                            main_stat_key = value.rstrip('_')
                        else:
                            # 位置4-6：百分比，保持原样
                            # hp_ → hp_, crit_ → crit_, etc.
                            main_stat_key = value
                        break
                if main_stat_key:
                    break

            # 主属性值通常是百分比或数字
            for i in range(main_stat_idx + 1, min(main_stat_idx + 5, len(texts))):
                match = re.search(r'(\d+(?:\.\d+)?)%?', texts[i])
                if match and texts[i] not in self.MAIN_STAT_MAP:
                    main_stat_value = float(match.group(1))
                    break

        # 默认值处理：如果解析失败，根据位置返回默认值
        if main_stat_key is None:
            main_stat_key = 'hp' if slot_key in ['1', '2', '3'] else 'hp_'

        return main_stat_key, main_stat_value

    def _parse_substats(self, ocr_items: List[Dict], main_stat_key: str) -> List[Dict]:
        """
        解析副属性
        副属性不能和主属性重复！需要过滤掉主属性
        
        OCR结果按Y坐标排序，相邻的文本属于同一行，通过顺序合并判断百分比
        """
        # 找到 "Sub-Stats" 或 "副属性" 关键字的索引
        sub_stat_idx = -1
        texts = []
        for i, item in enumerate(ocr_items):
            text = item.get('text', '')
            texts.append(text)
            if 'Sub-Stats' in text or 'Sub' in text or '副属性' in text:
                sub_stat_idx = i

        if sub_stat_idx < 0:
            return []

        # 提取Sub-Stats后的所有文本
        sub_texts = texts[sub_stat_idx + 1:]

        substats = []
        i = 0
        while i < len(sub_texts):
            text = sub_texts[i].strip()

            # 检查是否是属性名（可能包含升级标记）
            stat_key = None
            base_text = text
            upgrades = 0

            # 先尝试从文本中提取升级标记
            match = re.search(r'(.+?)\s*\+(\d+)', text)
            if match:
                base_text = match.group(1).strip()
                upgrades = int(match.group(2))

            # 匹配属性名
            for key, value in self.SUB_STAT_MAP.items():
                if key in base_text:
                    stat_key = value
                    break

            if stat_key:
                # 如果文本中没有升级标记，向后查找直到找到升级标记或数值
                if upgrades == 0:
                    j = i + 1
                    while j < len(sub_texts):
                        next_text = sub_texts[j].strip()
                        
                        # 检查是否是独立的升级标记
                        upgrade_match = re.match(r'^\+(\d+)$', next_text)
                        if upgrade_match:
                            upgrades = int(upgrade_match.group(1))
                            i = j  # 更新索引，跳过已处理的文本
                            break
                        
                        # 检查是否是纯数字或百分比数字（表示已到数值）
                        if re.match(r'^\d+(?:\.\d+)?%?$', next_text):
                            # 到达数值了，不再是升级标记
                            break
                        
                        j += 1
                
                # 副属性默认有1次升级，加上额外升级次数
                # 例如：+0表示1次，+1表示2次，+4表示5次
                upgrades += 1

                # 判断HP/ATK/DEF是否是百分比
                # 向后查找直到遇到纯数字或百分比数字
                if stat_key in ['hp', 'atk', 'def']:
                    is_percent = False
                    j = i + 1
                    while j < len(sub_texts):
                        next_text = sub_texts[j].strip()
                        
                        # 检查是否是纯数字或百分比数字
                        if re.match(r'^\d+(?:\.\d+)?%?$', next_text):
                            # 找到数值，判断是否包含%
                            if '%' in next_text:
                                is_percent = True
                            break
                        
                        j += 1
                    
                    if is_percent:
                        stat_key += '_'

                # **关键过滤：副属性不能和主属性重复！**
                if stat_key != main_stat_key:
                    substats.append({
                        'key': stat_key,
                        'upgrades': upgrades
                    })

            i += 1

        return substats

    def generate_export_json(self, discs: List[Dict]) -> str:
        """
        生成导出的JSON字符串

        Args:
            discs: 驱动盘数据列表

        Returns:
            格式化的JSON字符串
        """
        export_data = {
            'format': 'ZOD',
            'dbVersion': 2,
            'source': 'Zenless Optimizer',
            'version': 1,
            'discs': discs
        }

        return json.dumps(export_data, indent=2, ensure_ascii=False)


def test_ocr_first_image():
    """测试OCR第一个驱动盘图片"""
    import sys
    import os

    # 设置启动路径
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
    src_path = os.path.join(project_root, 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    import cv2
    from one_dragon.utils import os_utils
    from zzz_od.context.zzz_context import ZContext

    # 初始化上下文
    ctx = ZContext()
    ctx.init()

    # 获取第一个截图路径
    screenshots_dir = os_utils.get_path_under_work_dir('.debug', 'inventory_screenshots')
    image_path = os.path.join(screenshots_dir, 'drive_disk-0.jpg')

    if not os.path.exists(image_path):
        print(f"图片不存在: {image_path}")
        return

    # 读取图片
    screenshot = cv2.imread(image_path)
    if screenshot is None:
        print(f"读取图片失败: {image_path}")
        return

    print(f"正在OCR图片: {image_path}")
    print(f"图片尺寸: {screenshot.shape}")

    # 直接OCR
    ocr_result = ctx.ocr.run_ocr_single_line(screenshot)

    print("\n=== OCR结果 ===")
    if not ocr_result:
        print("OCR结果为空")
        return

    for idx, item in enumerate(ocr_result):
        if isinstance(item, str):
            print(f"{idx}: {item}")
        elif isinstance(item, dict):
            print(f"{idx}: {item}")
        else:
            text = item.text if hasattr(item, 'text') else str(item)
            score = item.score if hasattr(item, 'score') else 'N/A'
            print(f"{idx}: {text} (confidence: {score})")

    # 解析结果
    print("\n=== 解析结果 ===")
    parser = DriveDiskParser()

    # 转换OCR结果
    ocr_items = []
    for item in ocr_result:
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

    disc_data = parser.parse_ocr_result(ocr_items)
    if disc_data:
        import json
        print(json.dumps(disc_data, indent=2, ensure_ascii=False))
    else:
        print("解析失败")


if __name__ == '__main__':
    test_ocr_first_image()