import json
import re
from typing import Optional, Dict, List, Any


class DriveDiskParser:
    """驱动盘属性解析器，根据OCR结果生成JSON数据"""

    # 套装名称映射（关键词匹配）
    SET_NAME_MAP = {
        'Thunder': 'ThunderMetal',
        'Astral': 'AstralVoice',
        'Freedom': 'FreedomBlues',
        'Woodpecker': 'WoodpeckerElectro',
        'Hormone': 'HormonePunk',
        'Swing': 'SwingJazz',
        'Puffer': 'PufferElectro',
        'Polar': 'PolarMetal',
        'Fanged': 'FangedMetal',
        'Shockstar': 'ShockstarDisco',
        'Soul': 'SoulRock',
        'Inferno': 'InfernoMetal',
        'White': 'WhiteWaterBallad',
        'Chaos': 'ChaosMetal',
    }

    # 主属性映射（主属性都是百分比，需要加下划线后缀）
    MAIN_STAT_MAP = {
        'HP': 'hp_',
        'ATK': 'atk_',
        'DEF': 'def_',
        'CRIT Rate': 'crit_',
        'CRIT DMG': 'crit_dmg_',
        'Anomaly Proficiency': 'anomProf',
        'PEN Ratio': 'pen_',
        'Impact': 'impact',
        'Energy Regen': 'energyRegen_',  # ✅ 修正：能量回复是百分比
    }

    # 副属性映射（百分比属性需要下划线后缀）
    SUB_STAT_MAP = {
        'HP': 'hp_',          # HP百分比
        'ATK': 'atk_',        # ATK百分比
        'ATK.': 'atk_',       # ATK百分比（另一种写法）
        'DEF': 'def_',        # DEF百分比
        'CRIT Rate': 'crit_', # 暴击率（只有百分比）
        'CRIT DMG': 'crit_dmg_', # 暴击伤害（只有百分比）
        'Anomaly Proficiency': 'anomProf',
        'PEN': 'pen',         # PEN固定值
        'PEN Ratio': 'pen_',  # PEN百分比
        'Impact': 'impact',
    }

    def __init__(self):
        self.disc_counter = 0

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
        """解析套装名称（找到[数字]位置，[数字]前的所有文字都是套装名）"""
        # 找到包含[数字]的文本
        # 如: 'white', 'Water', 'Ballad [1]' → 应该取 'white' + 'Water' + 'Ballad'
        set_parts = []

        for text in texts:
            # 检查是否包含[数字]
            if re.search(r'\[\d\]', text):
                # 找到了[数字]，提取[数字]前的文字
                match = re.match(r'(.+?)\s*\[\d\]', text)
                if match:
                    # 取[数字]前的部分，首字母大写
                    part = match.group(1).strip()
                    if part:
                        set_parts.append(part.capitalize())
                # 找到[数字]后就停止
                break
            else:
                # [数字]前的文本，首字母大写后添加
                part = text.strip()
                if part:
                    set_parts.append(part.capitalize())

        if set_parts:
            # 合并所有部分并移除空格
            set_name = ''.join(set_parts).replace(' ', '')

            # 尝试映射到已知套装
            for key, value in self.SET_NAME_MAP.items():
                if key.lower() in set_name.lower():
                    return value

            return set_name

        # 默认返回第一个文本
        default_name = texts[0] if texts else 'Unknown'
        return default_name.capitalize().replace(' ', '')

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
            # 匹配 Lv. 15/15 或 Lv.15 格式
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

        # 找到 "Main Stat" 关键字
        main_stat_idx = -1
        for i, text in enumerate(texts):
            if 'Main Stat' in text or 'Main' in text:
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
        """
        # 找到 "Sub-Stats" 关键字的索引
        sub_stat_idx = -1
        texts = []
        for i, item in enumerate(ocr_items):
            text = item.get('text', '')
            texts.append(text)
            if 'Sub-Stats' in text or 'Sub' in text:
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
                # 如果文本中没有升级标记，查找下一个文本
                if upgrades == 0 and i + 1 < len(sub_texts):
                    next_text = sub_texts[i + 1].strip()
                    # 检查下一个是否是独立的升级标记
                    upgrade_match = re.match(r'^\+(\d+)$', next_text)
                    if upgrade_match:
                        upgrades = int(upgrade_match.group(1))
                        i += 1  # 跳过下一个文本

                # 判断是否是百分比属性
                # 查找接下来的几个文本中是否有百分比数值
                is_percent = False
                for j in range(i + 1, min(i + 5, len(sub_texts))):
                    if '%' in sub_texts[j]:
                        is_percent = True
                        break

                # 如果是百分比，添加下划线后缀
                if is_percent and not stat_key.endswith('_') and stat_key in ['hp', 'atk', 'def']:
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