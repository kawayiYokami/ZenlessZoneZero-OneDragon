import json
import os
import urllib.request
from typing import Dict


class TranslationUpdater:
    """翻译字典更新器"""

    API_URLS = {
        'character': 'https://api.hakush.in/zzz/data/character.json',
        'weapon': 'https://api.hakush.in/zzz/data/weapon.json',
        'equipment': 'https://api.hakush.in/zzz/data/equipment.json',
    }

    def __init__(self):
        self.dict_path = os.path.join(
            os.path.dirname(__file__),
            'translation_dict.json'
        )

    def update_all(self) -> bool:
        """更新所有翻译数据"""
        try:
            translation_dict = {
                'character': {},
                'weapon': {},
                'equipment': {}
            }

            # 更新角色
            print("正在下载角色数据...")
            char_data = self._download_json(self.API_URLS['character'])
            if char_data:
                translation_dict['character'] = self._extract_character_names(char_data)
                print(f"角色数据更新完成，共{len(translation_dict['character'])}个")

            # 更新音擎
            print("正在下载音擎数据...")
            weapon_data = self._download_json(self.API_URLS['weapon'])
            if weapon_data:
                translation_dict['weapon'] = self._extract_weapon_names(weapon_data)
                print(f"音擎数据更新完成，共{len(translation_dict['weapon'])}个")

            # 更新驱动盘
            print("正在下载驱动盘数据...")
            equipment_data = self._download_json(self.API_URLS['equipment'])
            if equipment_data:
                translation_dict['equipment'] = self._extract_equipment_names(equipment_data)
                print(f"驱动盘数据更新完成，共{len(translation_dict['equipment'])}个")

            # 保存字典
            self._save_dict(translation_dict)
            print(f"翻译字典已保存到: {self.dict_path}")
            return True

        except Exception as e:
            print(f"更新翻译字典失败: {e}")
            return False

    def _download_json(self, url: str) -> Dict:
        """下载JSON数据"""
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"下载失败 {url}: {e}")
            return {}

    def _extract_character_names(self, data: Dict) -> Dict:
        """提取角色名称"""
        result = {}
        for char_id, char_info in data.items():
            en_name = char_info.get('EN', '')
            if en_name:
                result[en_name] = {
                    'EN': en_name,
                    'CHS': char_info.get('CHS', ''),
                    'JA': char_info.get('JA', '')
                }
        return result

    def _extract_weapon_names(self, data: Dict) -> Dict:
        """提取音擎名称"""
        result = {}
        for weapon_id, weapon_info in data.items():
            en_name = weapon_info.get('EN', '')
            if en_name:
                result[en_name] = {
                    'EN': en_name,
                    'CHS': weapon_info.get('CHS', ''),
                    'JA': weapon_info.get('JA', '')
                }
        return result

    def _extract_equipment_names(self, data: Dict) -> Dict:
        """提取驱动盘名称"""
        result = {}
        for equip_id, equip_info in data.items():
            en_data = equip_info.get('EN', {})
            chs_data = equip_info.get('CHS', {})
            ja_data = equip_info.get('JA', {})

            en_name = en_data.get('name', '') if isinstance(en_data, dict) else ''
            if en_name:
                result[en_name] = {
                    'EN': en_name,
                    'CHS': chs_data.get('name', '') if isinstance(chs_data, dict) else '',
                    'JA': ja_data.get('name', '') if isinstance(ja_data, dict) else ''
                }
        return result

    def _save_dict(self, translation_dict: Dict):
        """保存翻译字典"""
        with open(self.dict_path, 'w', encoding='utf-8') as f:
            json.dump(translation_dict, f, ensure_ascii=False, indent=2)


def __debug():
    """测试更新"""
    updater = TranslationUpdater()
    updater.update_all()


if __name__ == '__main__':
    __debug()
