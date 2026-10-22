# 贝果物品角标素材

本目录统一保存物品角标的来源图、局内参考帧和模板重建参数。画面规则见[贝果格子说明](../../贝果计划.md#格子)，分类与优先级见[自动化说明](../../../../develop/zzz/application/bagel.md#物品识别与优先级)。

归档图均为 1920×1080，不缩放；右下 `[1780,1050,1920,1080)` 涂黑遮挡账号标识（UID），物品格保持原样。来源和裁剪参数统一记录在本文，不另建清单。

## 仓库源图

仓库图的来源为上传 PNG，归档格式为无损 WebP。遮挡区域以外的像素与源文件一致。上传前的采集与压缩历史未知，不能仅凭 PNG 扩展名认定为原始采集像素。

| 文件 | 画面 | 用途 |
| --- | --- | --- |
| [warehouse_blueprint.webp](warehouse_blueprint.webp) | 详情显示「[蓝图]骸影·太初梦魔」；选中格左上是白色卷页 | 核对蓝图类型与真实格子角标 |
| [warehouse_anticorrosion.webp](warehouse_anticorrosion.webp) | 详情显示「高阶抗蚀器」；蓝图所在行没有选中边框 | 蓝图模板裁剪源、非蓝图对照 |
| [warehouse_backpack.webp](warehouse_backpack.webp) | 详情显示「邦邦大背包」 | 背包模板裁剪源、非蓝图对照 |
| [warehouse_blade.webp](warehouse_blade.webp) | 详情显示「高阶异体刃·强斩」 | 非蓝图对照 |

仓库非蓝图详情图的右侧仍显示蓝图。`warehouse_blueprint.webp` 中蓝图位于第三可见行，其余图中位于第二可见行。顶部一行角标被标题遮挡，不作为完整角标样本。

## 局内录像帧

录像标识为 `2026-10-20 10-13-33.flv`，视频流为 1920×1080、60 FPS。通过 OpenCV `VideoCapture` 按毫秒定位后读取，将 BGR 转为 RGB、遮挡 UID，再无损保存。原录像不在仓内；归档帧可独立用于识别复核，不依赖外部录像路径。

无损编码保留视频解码像素，不恢复录像压缩前的采集精度。单帧只支持角标和品质的核对，不能证明连续拖拽、对换或结算成功。

| 文件 | 相对录像时间 | 可核对范围 |
| --- | --- | --- |
| [search_result_valuable_reward.webp](search_result_valuable_reward.webp) | 00:38:00 | 电子保险箱搜索结果第 5 格为 Z 品质，使用第二种钻石角标；安全箱第 3 格使用原有钻石角标 |
| [search_safe_valuable_reward.webp](search_safe_valuable_reward.webp) | 00:38:02 | 同一物品在安全箱第 5 格，角标完整；第 3 格仍有原有钻石角标 |
| [search_safe_blueprint.webp](search_safe_blueprint.webp) | 01:06:30 | 电子保险箱搜查中；安全箱第 3 格显示 Z 蓝图，格心 `(461,899)`；相邻格为非蓝图 |
| [search_result_blueprint.webp](search_result_blueprint.webp) | 01:42:30 | 武备箱搜查中；搜索结果第 2 格显示 S 蓝图，格心 `(1443,330)`；第 1 格为 Z 非蓝图，第 3 格正在揭示 |
| [search_result_backpack.webp](search_result_backpack.webp) | 03:05:25 | 武备箱搜索结果第 4 格为 A 品质背包，左上为背包角标 |
| [search_safe_backpack.webp](search_safe_backpack.webp) | 03:05:29 | A 品质背包位于安全箱第 4 格；相邻第 2 格为蓝图，第 3 格为普通战术道具 |

第二种钻石角标对应菲林／零号业绩，类型含义来自维护者说明。局内帧包含菲林外观，但未打开详情；零号业绩的独立物品格样本尚未归档。

普通骸影的同名对照另见测试仓 `zzz-od-test/screens/贝果-备战/clear_loadout_dense_detail.webp`，来源索引见[启动清空相关画面](../../贝果计划.md#启动清空相关画面)。该图详情显示「骸影·太初梦魔」，左侧物品格为战术道具角标，不是卷页角标。不能按名称将普通骸影视为蓝图。

## 模板重建

下表源图均位于本目录。裁剪矩形包含左上边界，不包含右下边界。三个模板均为 24×26 像素，保留完整白色图案和接近对称的留白。

| 模板 | 源图 | 裁剪矩形 |
| --- | --- | --- |
| [blueprint.png](../../../../../assets/game_data/bagel/item_badges/blueprint.png) | `warehouse_anticorrosion.webp` | `[1488,360,1512,386)` |
| [valuable_reward.png](../../../../../assets/game_data/bagel/item_badges/valuable_reward.png) | `search_result_valuable_reward.webp` | `[1697,272,1721,298)` |
| [backpack.png](../../../../../assets/game_data/bagel/item_badges/backpack.png) | `warehouse_backpack.webp` | `[1280,605,1304,631)` |

重建步骤：

1. 读取表中源图的 RGB 像素。
2. 裁取对应矩形。
3. 原样保存为 RGB PNG。
4. 将裁图与运行模板逐像素比较。

不缩放、不掩码、不补边、不锐化、不重新居中。模板均来自真实物品格，不使用详情栏或筛选按钮图标。

蓝图模板取自仓库右侧第二可见行第五格。背包模板选择仓库未选中格，避开黄色边框。两者像素均与上传 PNG 的相同矩形一致，不从录像帧裁取。第二种钻石模板使用录像解码像素，保留其压缩限制，不能称为原始截图模板。

## 素材范围

- 仓库源图包含蓝图、普通抗蚀器、普通异体刃和普通背包。局内蓝图样本仅包含 S 搜索结果格和 Z 安全箱格。
- 尚无 A/B/C/未知品质蓝图，以及同名普通骸影与蓝图的连续对照素材。不能据现有样本推定全部蓝图外观均适用。
- 搜索结果格和安全箱格均包含第二种钻石与背包角标。相邻格可用于对照原有钻石、蓝图、战术道具、战术棱镜、材料和金币。
- 背包模板来自仓库 S 品质物品，局内对照为 A 品质物品。不能据此推定所有品质和全部背包外观均适用。
- 物品揭示或拖拽期间，角标可能尚未出现或暂时隐藏。这类帧不能单独证明存在未知物品类型。
- 自动化执行的未知范围统一见[入箱识别与执行限制](../../../../develop/zzz/application/bagel.md#入箱识别与执行限制)。
