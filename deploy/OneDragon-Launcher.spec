# -*- mode: python ; coding: utf-8 -*-

import struct
import tempfile
from pathlib import Path


def _extract_icon_png(ico_path: str) -> str:
    """PyInstaller 的启动画面只认 PNG，而仓库里的图标只有一份 ico，
    这里把 ico 里那帧 PNG 取出来写到临时目录，不再往仓库塞一份重复的图。
    ico 的 256x256 帧本身就是 PNG 数据，解析目录项即可，不用图像库。"""
    raw = Path(ico_path).read_bytes()
    count = struct.unpack('<H', raw[4:6])[0]
    best_size = -1
    best_data = None
    for i in range(count):
        entry = 6 + i * 16
        width = raw[entry] or 256
        size, offset = struct.unpack('<II', raw[entry + 8:entry + 16])
        chunk = raw[offset:offset + size]
        if chunk[:8] != b'\x89PNG\r\n\x1a\n':
            continue
        if width > best_size:
            best_size = width
            best_data = chunk
    if best_data is None:
        raise SystemExit('图标里没有 PNG 帧，无法生成启动画面')
    out_path = Path(tempfile.gettempdir()) / 'onedragon_launcher_splash.png'
    out_path.write_bytes(best_data)
    return str(out_path)


a = Analysis(
    ['..\\src\\zzz_od\\win_exe\\launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('../config/project.yml', 'resources/config'),
        ('../config/repository.yml', 'resources/config'),
        # 进度窗口里居中显示的图标
        ('../assets/ui/logo.ico', 'resources/assets/ui'),
    ],
    hiddenimports=['_cffi_backend'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 启动器只做代码更新、依赖同步和拉起主界面，游戏截图、界面控件、图像处理都用不到，
    # 这些包被间接引用链带进来只会撑大体积，明确排除。
    # tkinter 不能排除：启动画面本身就是 Tcl/Tk 画的，排掉会缺 _tk_data/tk.tcl。
    excludes=[
        'win32ui',
        'Pythonwin',
        'setuptools',
        'pkg_resources',
        'pydoc_data',
        'numpy',
        'cv2',
        'PIL',
        'matplotlib',
        'PySide6',
        'pyside6',
        'onnxruntime',
        'shapely',
        'pyclipper',
        'soundcard',
        'librosa',
        'gensim',
        'scipy',
        'cryptography',
        'pyexpat',
        'xml',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 单文件启动器要先把自己解压到临时目录，这段时间屏幕上什么都没有。
# 这张图由 bootloader 直接弹出，等 Python 起来交给 LaunchSplash 接管进度，
# 所以这里不放文字和进度，只求把空窗盖住。窗口尺寸等于图片尺寸。
splash = Splash(
    _extract_icon_png('..\\assets\\ui\\logo.ico'),
    binaries=a.binaries,
    datas=a.datas,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    # 启动画面是 Tcl/Tk 画的，这几个运行时数据（_tk_data/tk.tcl 等）必须一起进包，
    # 只传 a.binaries 的话 bootloader 会报 could not find requirement _tk_data\tk.tcl
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name='OneDragon-Launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    icon=['..\\assets\\ui\\logo.ico'],
)
