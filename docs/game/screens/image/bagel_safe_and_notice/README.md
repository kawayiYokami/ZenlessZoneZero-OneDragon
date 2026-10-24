# 安全箱素材

本目录保存锁定格与时钟空格的代表画面。画面规则见[贝果计划画面](../../贝果计划.md)，分类规则见[贝果自动化](../../../../develop/zzz/application/bagel.md#收集与对换)。文件身份校验值见 [sha256.json](sha256.json)。

## 锁定格

[safe_four_locked.jpg](safe_four_locked.jpg) 原字节复制自 [Cora093/ZenlessZoneZero-OneDragon#69](https://github.com/Cora093/ZenlessZoneZero-OneDragon/issues/69) 的[附件](https://gist.githubusercontent.com/Cora093/7e1be2756ea39309bfb309a226e6fb20/raw/338c41e86dabb5385b7e561d7e9bc688082adfc4/issue-non5-safebox-lock.jpg)。文件为 1920×1080、RGB JPEG，色度采样为 4:2:0，不含 EXIF。附件已经遮挡右下账号标识，安全箱区域未遮挡。本目录未再次缩放或压缩。

安全箱前四格有物品，第五格显示挂锁和 `LOCK`。附件正文声明原生 1080p；文件本身只能证明当前尺寸，不能独立核实上传前是否缩放。JPEG 有损像素可用于故障重现和压缩兼容核对，不能作为原始无损采集。现有挂锁模板继续使用项目原有文件，未从该 JPEG 重新裁取。

## 时钟空格

[warehouse_empty_clock.png](warehouse_empty_clock.png) 来自录像 `Zenless Zone Zero 2026.10.24 - 00.41.12.04.DVR.mp4` 的 00:21.50。安全箱五格均空，第四、第五格有时钟图标。时钟空格属于可用格，不能与挂锁视为同一状态。

源录像尺寸为 3840×2160，标称 60 FPS，总长约 36.281 秒，编码为 H.264、YUV 4:2:0，颜色标记为 BT.709。原录像未纳入仓库；本图可独立复核，不依赖外部文件路径。

帧处理顺序：FFmpeg 按相对录像起点的时间解码为 PNG；OpenCV 按默认 `INTER_LINEAR` 缩至 1920×1080；BGR 转 RGB；将右下矩形 `[1780,1050,1920,1080)` 涂黑遮挡账号标识；保存为 PNG。矩形包含左上、不包含右下。无损 PNG 仅保留处理后的解码像素，不能恢复录像压缩前的精度，也不能称为原生 1080p。

## 适用限制

- 附件仅包含四格容量的局内满箱画面；两格、三格和小容量仓库没有本目录的真实样本。
- 挂锁与时钟对照支持区分这两种图案，不证明全部光照、动画和物品外观均已覆盖。
- 静态图可用于识别复核，不能证明自动化发送的输入被游戏接受。
