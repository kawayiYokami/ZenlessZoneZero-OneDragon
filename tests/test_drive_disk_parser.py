"""测试驱动盘解析器"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from zzz_od.application.inventory_scan.parser.drive_disk_parser import DriveDiskParser


def test_white_water_ballad():
    """测试 WhiteWaterBallad 套装名称解析"""
    parser = DriveDiskParser()
    
    # 模拟OCR结果：white, Water, Ballad [1]
    ocr_texts = [
        {'text': 'white', 'confidence': 0.91, 'position': (7, 4, 113, 35)},
        {'text': 'Water', 'confidence': 0.98, 'position': (100, 3, 212, 34)},
        {'text': 'Ballad [1]', 'confidence': 0.98, 'position': (7, 30, 172, 64)},
        {'text': 'RARITY', 'confidence': 0.98, 'position': (335, 95, 369, 109)},
        {'text': 'Lv. 15/15', 'confidence': 0.99, 'position': (58, 138, 187, 169)},
        {'text': 'EMPTY', 'confidence': 0.87, 'position': (290, 143, 350, 163)},
        {'text': 'Main Stat', 'confidence': 1.00, 'position': (21, 190, 135, 214)},
        {'text': 'HP', 'confidence': 0.99, 'position': (20, 226, 61, 254)},
        {'text': '2,200', 'confidence': 0.99, 'position': (339, 226, 410, 260)},
        {'text': 'Sub-Stats', 'confidence': 1.00, 'position': (21, 266, 138, 292)},
        {'text': 'CRIT Rate', 'confidence': 0.94, 'position': (21, 304, 145, 332)},
        {'text': '2.4%', 'confidence': 0.99, 'position': (348, 305, 411, 336)},
        {'text': 'ATK', 'confidence': 0.99, 'position': (20, 355, 76, 386)},
        {'text': '3%', 'confidence': 0.95, 'position': (368, 355, 412, 387)},
        {'text': 'PEN +1', 'confidence': 0.98, 'position': (20, 408, 108, 436)},
        {'text': '18', 'confidence': 1.00, 'position': (374, 407, 412, 437)},
        {'text': 'CRIT DMG', 'confidence': 0.98, 'position': (23, 461, 142, 485)},
        {'text': '+3', 'confidence': 0.98, 'position': (140, 464, 172, 484)},
        {'text': '19.2%', 'confidence': 1.00, 'position': (337, 458, 411, 490)},
    ]
    
    result = parser.parse_ocr_result(ocr_texts)
    
    print("=" * 60)
    print("测试用例：WhiteWaterBallad")
    print("=" * 60)
    print(f"输入OCR文本: {[item['text'] for item in ocr_texts[:3]]}")
    print(f"解析结果: setKey = {result['setKey']}")
    print(f"期望结果: setKey = WhiteWaterBallad")
    print(f"测试结果: {'✅ 通过' if result['setKey'] == 'WhiteWaterBallad' else '❌ 失败'}")
    print()
    print(f"完整解析结果:")
    print(f"  - 套装: {result['setKey']}")
    print(f"  - 位置: {result['slotKey']}")
    print(f"  - 等级: {result['level']}")
    print(f"  - 主属性: {result['mainStatKey']}")
    print(f"  - 副属性数量: {len(result['substats'])}")
    print("=" * 60)
    
    assert result['setKey'] == 'WhiteWaterBallad', f"期望 WhiteWaterBallad，实际 {result['setKey']}"


def test_thunder_metal():
    """测试 ThunderMetal 套装名称解析"""
    parser = DriveDiskParser()
    
    # 模拟OCR结果：Thunder Metal [4]
    ocr_texts = [
        {'text': 'Thunder', 'confidence': 0.95},
        {'text': 'Metal [4]', 'confidence': 0.98},
        {'text': 'Lv. 12/15', 'confidence': 0.99},
        {'text': 'Main Stat', 'confidence': 1.00},
        {'text': 'ATK', 'confidence': 0.99},
        {'text': '420', 'confidence': 0.99},
    ]
    
    result = parser.parse_ocr_result(ocr_texts)
    
    print("=" * 60)
    print("测试用例：ThunderMetal")
    print("=" * 60)
    print(f"输入OCR文本: {[item['text'] for item in ocr_texts[:2]]}")
    print(f"解析结果: setKey = {result['setKey']}")
    print(f"期望结果: setKey = ThunderMetal")
    print(f"测试结果: {'✅ 通过' if result['setKey'] == 'ThunderMetal' else '❌ 失败'}")
    print("=" * 60)
    
    assert result['setKey'] == 'ThunderMetal', f"期望 ThunderMetal，实际 {result['setKey']}"


def test_capitalize():
    """测试首字母大写"""
    parser = DriveDiskParser()
    
    # 模拟OCR结果：小写的 white water ballad
    ocr_texts = [
        {'text': 'white', 'confidence': 0.91},
        {'text': 'water', 'confidence': 0.98},
        {'text': 'ballad [1]', 'confidence': 0.98},
        {'text': 'Lv. 15/15', 'confidence': 0.99},
    ]
    
    result = parser.parse_ocr_result(ocr_texts)
    
    print("=" * 60)
    print("测试用例：首字母大写")
    print("=" * 60)
    print(f"输入OCR文本: {[item['text'] for item in ocr_texts[:3]]}")
    print(f"解析结果: setKey = {result['setKey']}")
    print(f"说明: 所有单词首字母都应该大写")
    print(f"测试结果: {'✅ 通过' if result['setKey'] == 'WhiteWaterBallad' else '❌ 失败'}")
    print("=" * 60)
    
    assert result['setKey'] == 'WhiteWaterBallad', f"期望 WhiteWaterBallad，实际 {result['setKey']}"


if __name__ == '__main__':
    print("\n🧪 开始测试驱动盘解析器\n")
    
    try:
        test_white_water_ballad()
        print()
        test_thunder_metal()
        print()
        test_capitalize()
        print()
        print("✅ 所有测试通过！")
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)