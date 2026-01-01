"""测试副属性解析逻辑"""


def group_by_row(ocr_items, y_threshold=20):
    """
    将OCR结果按行分组
    
    Args:
        ocr_items: OCR识别结果，每项包含 text 和 position (x1, y1, x2, y2)
        y_threshold: Y坐标差异阈值，小于此值认为在同一行
    
    Returns:
        按行分组的文本列表，每行是一个字典 {'texts': [...], 'y': avg_y}
    """
    rows = []
    
    for item in ocr_items:
        text = item['text']
        x1, y1, x2, y2 = item['position']
        y_center = (y1 + y2) / 2
        
        # 查找是否有匹配的行
        found_row = None
        for row in rows:
            if abs(row['y'] - y_center) < y_threshold:
                found_row = row
                break
        
        if found_row:
            # 添加到现有行
            found_row['texts'].append(text)
            # 更新平均Y坐标
            found_row['y'] = (found_row['y'] * len(found_row['texts']) + y_center) / (len(found_row['texts']) + 1)
        else:
            # 创建新行
            rows.append({
                'texts': [text],
                'y': y_center
            })
    
    # 按Y坐标排序
    rows.sort(key=lambda r: r['y'])
    
    return rows


def parse_substat_row(row_texts):
    """
    解析一行副属性文本
    
    Args:
        row_texts: 同一行的所有文本，例如 ['ATK +2', '9%']
    
    Returns:
        (stat_key, upgrades) 或 None
    """
    import re
    
    # 副属性映射（基础值，不带下划线）
    SUB_STAT_MAP = {
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
    }
    
    stat_key = None
    upgrades = 0
    is_percent = False
    
    # 合并所有文本，用空格分隔
    combined_text = ' '.join(row_texts)
    
    # 检查是否包含百分比符号
    is_percent = '%' in combined_text
    
    # 提取属性名和升级次数
    for text in row_texts:
        # 尝试匹配 "属性名 +数字" 格式
        match = re.search(r'(.+?)\s*\+(\d+)', text)
        if match:
            base_text = match.group(1).strip()
            upgrades = int(match.group(2))
            
            # 匹配属性名
            for key, value in SUB_STAT_MAP.items():
                if key in base_text:
                    stat_key = value
                    break
        else:
            # 尝试匹配纯属性名（没有升级标记）
            for key, value in SUB_STAT_MAP.items():
                if key in text:
                    stat_key = value
                    break
    
    if stat_key:
        # 如果是hp/atk/def且包含百分比，添加下划线
        if stat_key in ['hp', 'atk', 'def'] and is_percent:
            stat_key += '_'
        
        return stat_key, upgrades
    
    return None


def test_substat_parsing():
    """测试副属性解析"""
    
    # 模拟OCR结果
    ocr_items = [
        {'text': 'Sub-Stats', 'position': (23, 269, 137, 294)},
        {'text': 'CRIT DMG +1', 'position': (23, 306, 172, 333)},
        {'text': '9.6%', 'position': (348, 305, 410, 336)},
        {'text': 'DEF', 'position': (21, 356, 76, 386)},
        {'text': '4.8%', 'position': (349, 357, 411, 387)},
        {'text': 'ATK +2', 'position': (21, 408, 109, 436)},
        {'text': '9%', 'position': (367, 407, 412, 439)},
        {'text': 'PEN +1', 'position': (19, 458, 109, 489)},
        {'text': '18', 'position': (373, 457, 413, 490)},
    ]
    
    # 找到 Sub-Stats 的索引
    sub_stat_idx = -1
    for i, item in enumerate(ocr_items):
        if 'Sub-Stats' in item['text']:
            sub_stat_idx = i
            break
    
    # 提取 Sub-Stats 后的所有文本
    sub_items = ocr_items[sub_stat_idx + 1:]
    
    # 按行分组
    rows = group_by_row(sub_items)
    
    print("=== 按行分组结果 ===")
    for i, row in enumerate(rows):
        print(f"第{i+1}行 (Y={row['y']:.1f}): {row['texts']}")
    
    print("\n=== 解析结果 ===")
    substats = []
    for row in rows:
        result = parse_substat_row(row['texts'])
        if result:
            stat_key, upgrades = result
            substats.append({'key': stat_key, 'upgrades': upgrades})
            print(f"属性: {stat_key}, 升级次数: {upgrades}")
    
    print("\n=== 最终副属性 ===")
    print(substats)
    
    # 验证结果
    expected = [
        {'key': 'crit_dmg_', 'upgrades': 1},
        {'key': 'def_', 'upgrades': 0},  # DEF 4.8% 是百分比
        {'key': 'atk_', 'upgrades': 2},  # ATK 9% 是百分比
        {'key': 'pen', 'upgrades': 1},   # PEN 18 是固定值
    ]
    
    print("\n=== 预期结果 ===")
    print(expected)
    
    print("\n=== 对比结果 ===")
    if substats == expected:
        print("✅ 测试通过！")
    else:
        print("❌ 测试失败！")
        print(f"实际: {substats}")
        print(f"预期: {expected}")


if __name__ == '__main__':
    test_substat_parsing()