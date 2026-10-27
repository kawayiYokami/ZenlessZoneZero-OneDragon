"""启动过程的图形化进度窗口。

在启动器原有流程外面套一层无边框网页窗口，把阶段性进度推给页面显示。
进度由工作线程主动推给页面，页面只负责渲染；这样既不阻塞启动流程，
也避免页面轮询接口和窗口关闭争抢同一个 UI 线程。

界面配色跟随系统深浅色主题，背景铺英文名水印，logo 居中显示。
窗口不可用（缺少 pywebview、缺少 WebView2、创建失败）时静默降级为纯命令行输出，
保证启动流程本身不受影响。
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import struct
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from one_dragon.utils.log_utils import log

_SPLASH_WIDTH = 520
_SPLASH_HEIGHT = 300

# 主界面出现的最长等待时间，超时后直接收尾避免窗口挂死
_MAIN_WINDOW_TIMEOUT = 180.0
# 看到主界面窗口后，再等它画完第一帧的时间，之后即便没有就绪文件也收尾
_MAIN_WINDOW_GRACE = 1.2
# 结束状态在窗口上停留的时间，让用户看清最后一步
_FINISH_HOLD = 0.6
# 主窗口轮询间隔
_POLL_INTERVAL = 0.2

# 深色主题下的窗口底色，用于页面加载前的过渡，避免白闪
_BACKGROUND_DARK = '#0E0E11'
_BACKGROUND_LIGHT = '#F7F7FA'

# 背景水印文案
_WATERMARK_TEXT = 'ONE DRAGON'


class LaunchError(Exception):
    """启动流程中需要直接展示给用户的失败，闪屏会把消息显示出来而不是静默退出。"""

# 进度上报回调：百分比 + 文案
ProgressReporter = Callable[[int, str], None]
# 启动任务：接收进度上报回调与"等待主界面"的回调，执行真正的启动流程
LaunchTask = Callable[[ProgressReporter, Callable[[], None]], None]


_SPLASH_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; overflow: hidden; }

  body {
    --bg: #0E0E11;
    --bg-top: #17131B;
    --fg: #EDEDF2;
    --muted: #918D9B;
    /* 主色取自 logo 的珊瑚红，冷色只作辅助压住暖调的腻 */
    --warm: rgba(255, 121, 111, 0.20);
    --soft: rgba(255, 174, 120, 0.10);
    --cool: rgba(126, 96, 196, 0.16);
    --watermark: rgba(255, 255, 255, 0.062);
    --accent: #FF796F;
    --accent-2: #FFC06B;
    --track: rgba(255, 255, 255, 0.09);
    --edge: rgba(255, 255, 255, 0.07);
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    background: var(--bg);
    color: var(--fg);
  }

  body.light {
    --bg: #F7F6FA;
    --bg-top: #FFFFFF;
    --fg: #1B1B21;
    --muted: #78747F;
    --warm: rgba(232, 86, 74, 0.095);
    --soft: rgba(255, 174, 120, 0.20);
    --cool: rgba(126, 96, 196, 0.10);
    --watermark: rgba(0, 0, 0, 0.030);
    --accent: #E8564A;
    --accent-2: #ED9A3C;
    --track: rgba(0, 0, 0, 0.10);
    --edge: rgba(0, 0, 0, 0.07);
  }

  .card {
    position: relative;
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--edge);
    border-radius: 12px;
    overflow: hidden;
    /* 三层径向光晕叠在斜向基底渐变上，让底色有层次而不是一块死色 */
    background:
      radial-gradient(120% 96% at 84% 6%, var(--warm) 0%, rgba(0, 0, 0, 0) 58%),
      radial-gradient(88% 84% at 10% 96%, var(--cool) 0%, rgba(0, 0, 0, 0) 62%),
      radial-gradient(64% 64% at 46% 44%, var(--soft) 0%, rgba(0, 0, 0, 0) 68%),
      linear-gradient(158deg, var(--bg-top) 0%, var(--bg) 72%);
  }

  .drag { position: absolute; inset: 0; z-index: 4; }

  /* 背景英文名水印：左上角的水平小字 */
  .watermark {
    position: absolute;
    inset: 0;
    z-index: 0;
    overflow: hidden;
    pointer-events: none;
    user-select: none;
  }
  .watermark span {
    position: absolute;
    left: 13px;
    top: 11px;
    font-size: 40px;
    font-weight: 900;
    letter-spacing: 0.03em;
    line-height: 1;
    white-space: nowrap;
    color: var(--watermark);
  }

  /* 居中的 logo */
  .stage {
    position: relative;
    z-index: 2;
    display: flex;
    flex-direction: column;
    align-items: center;
    transform: translateY(-8px);
  }
  .logo {
    width: 74px;
    height: 74px;
    border-radius: 18px;
    object-fit: contain;
    filter: drop-shadow(0 6px 18px rgba(0, 0, 0, 0.20));
  }
  .brand {
    margin-top: 13px;
    font-size: 12.5px;
    letter-spacing: 0.16em;
    color: var(--muted);
  }

  /* 底部进度 */
  .bottom {
    position: absolute;
    left: 38px;
    right: 38px;
    bottom: 26px;
    z-index: 2;
  }
  .line {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 9px;
  }
  .text { font-size: 12.5px; color: var(--muted); letter-spacing: 0.04em; }
  .percent { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .bar {
    position: relative;
    width: 100%;
    height: 2px;
    border-radius: 1px;
    background: var(--track);
    overflow: hidden;
  }
  .fill {
    position: absolute;
    left: 0;
    top: 0;
    height: 100%;
    width: 0;
    background: linear-gradient(90deg, var(--accent), var(--accent-2));
    transition: width 0.55s cubic-bezier(0.4, 0, 0.2, 1);
  }

  /* 失败提示覆盖整块卡片 */
  .error {
    display: none;
    position: absolute;
    inset: 0;
    z-index: 3;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    padding: 0 46px;
    text-align: center;
    background: var(--bg);
  }
  .error-title { font-size: 13px; color: var(--accent); letter-spacing: 0.12em; }
  .error-text {
    font-size: 12.5px;
    color: var(--muted);
    line-height: 1.7;
    max-height: 96px;
    overflow-y: auto;
  }
  .btn {
    padding: 7px 24px;
    border: 1px solid var(--edge);
    border-radius: 6px;
    background: transparent;
    color: var(--fg);
    font-size: 12.5px;
    cursor: pointer;
  }
  .btn:hover { background: var(--track); }

  .fade { opacity: 0; transition: opacity 0.45s ease; }
</style>
</head>
<body class="__THEME__">
  <div class="card" id="card">
    <div class="drag pywebview-drag-region"></div>
    <div class="watermark">
      <span>__WATERMARK__</span>
    </div>
    <div class="stage">
      __LOGO__
      <div class="brand">绝区零 一条龙</div>
    </div>
    <div class="bottom" id="bottom">
      <div class="line">
        <span class="text" id="text">正在准备</span>
        <span class="percent"><b id="num">0</b>%</span>
      </div>
      <div class="bar">
        <div class="fill" id="fill"></div>
      </div>
    </div>
    <div class="error" id="error">
      <div class="error-title">启动没有完成</div>
      <div class="error-text" id="error-text"></div>
      <button class="btn" onclick="window.close();">关闭</button>
    </div>
  </div>
<script>
  window.setProgress = function (percent, text) {
    document.getElementById('num').textContent = percent;
    document.getElementById('text').textContent = text;
    document.getElementById('fill').style.width = percent + '%';
  };
  window.markDone = function () {
    window.setProgress(100, '准备完成');
    document.getElementById('card').classList.add('done');
  };
  window.setError = function (text) {
    document.getElementById('bottom').style.display = 'none';
    document.getElementById('error').style.display = 'flex';
    document.getElementById('error-text').textContent = text;
  };
  window.fadeOut = function () {
    document.getElementById('card').classList.add('fade');
  };
</script>
</body>
</html>
"""


def _is_light_theme() -> bool:
    """系统是否使用浅色主题。读不到时按深色处理。"""
    try:
        import winreg

        key_path = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            return bool(value)
    except Exception as error:
        log.debug(f'读取系统主题失败，按深色处理：{error}')
        return False


def _extract_png_from_ico(raw: bytes) -> bytes | None:
    """取出 ico 里最大的 PNG 帧。ico 的 256x256 帧本身通常就是 PNG 数据，不必依赖图像库"""
    if len(raw) < 6:
        return None
    count = struct.unpack('<H', raw[4:6])[0]
    best_size = -1
    best_data: bytes | None = None
    for i in range(count):
        entry = 6 + i * 16
        if entry + 16 > len(raw):
            break
        width = raw[entry] or 256
        size, offset = struct.unpack('<II', raw[entry + 8:entry + 16])
        chunk = raw[offset:offset + size]
        if chunk[:8] != b'\x89PNG\r\n\x1a\n':
            continue
        if width > best_size:
            best_size = width
            best_data = chunk
    return best_data


def _logo_html() -> str:
    """把图标转成内联图片标签，读不到图标时返回空串。"""
    try:
        from one_dragon.utils import os_utils

        icon_path = os_utils.get_resource_path('assets', 'ui', 'logo.ico')
        if not os.path.isfile(icon_path):
            log.warning(f'启动进度窗口找不到图标：{icon_path}')
            return ''
        with open(icon_path, 'rb') as file:
            png = _extract_png_from_ico(file.read())
        if png is None:
            log.warning(f'启动进度窗口的图标里没有 PNG 帧：{icon_path}')
            return ''
        encoded = base64.b64encode(png).decode('ascii')
        return f'<img class="logo" src="data:image/png;base64,{encoded}" alt="">'
    except Exception as error:
        log.warning(f'启动进度窗口加载图标失败：{error}')
        return ''


def _build_html(light: bool) -> str:
    """按主题生成页面内容。"""
    return (
        _SPLASH_HTML_TEMPLATE
        .replace('__THEME__', 'light' if light else 'dark')
        .replace('__WATERMARK__', _WATERMARK_TEXT)
        .replace('__LOGO__', _logo_html())
    )


class LaunchSplash:
    """无边框启动进度窗口。

    窗口不可用时所有方法都是无害的空操作，启动任务照常执行。
    """

    def run(self, task: LaunchTask) -> None:
        """显示进度窗口并执行启动任务。

        窗口占据主线程（WebView 的事件循环要求），启动任务在子线程执行。
        任务拉起主界面后，启动器自己盯着桌面上的主界面窗口，看到它出现就淡出关闭；
        失败时切到错误状态等用户关闭。
        """
        try:
            import webview
        except Exception as error:
            log.warning(f'启动进度窗口不可用，改用命令行输出：{error}')
            task(_noop_reporter, _noop_wait_main_window)
            return

        light = _is_light_theme()

        splash_x, splash_y = _centered_position(_SPLASH_WIDTH, _SPLASH_HEIGHT)
        try:
            window = webview.create_window(
                '一条龙启动中',
                html=_build_html(light),
                width=_SPLASH_WIDTH,
                height=_SPLASH_HEIGHT,
                x=splash_x,
                y=splash_y,
                frameless=True,
                easy_drag=False,
                shadow=True,
                resizable=False,
                background_color=_BACKGROUND_LIGHT if light else _BACKGROUND_DARK,
            )
        except Exception as error:
            log.warning(f'启动进度窗口创建失败，改用命令行输出：{error}')
            task(_noop_reporter, _noop_wait_main_window)
            return

        exit_code: list[int] = []
        loaded = threading.Event()

        def report(percent: int, text: str) -> None:
            _run_js(window, f'window.setProgress({percent}, {json.dumps(text)})')

        def wait_main_window() -> None:
            _wait_main_window(report)

        def on_loaded() -> None:
            loaded.set()

        def worker() -> None:
            # 进度窗口已经接管显示，控制台再留着只会挡住画面
            _hide_console()
            # 页面没加载完就写进度会失败，必须等首次加载完成
            if not loaded.wait(timeout=15):
                log.warning('启动进度页面加载超时，继续执行启动流程')
            try:
                task(report, wait_main_window)
                _run_js(window, 'window.markDone()')
                time.sleep(_FINISH_HOLD)
                _run_js(window, 'window.fadeOut()')
                time.sleep(0.45)
            except LaunchError as error:
                log.error(error.message)
                _run_js(window, f'window.setError({json.dumps(error.message)})')
                exit_code.append(1)
            except Exception:
                log.exception('启动流程出现未处理异常')
                _run_js(window, 'window.setError("启动过程中出现未处理的异常，请查看日志后重试")')
                exit_code.append(1)
            finally:
                _close(window)
                # 失败时窗口要留在屏幕上等用户看完，不能自动消失
                if exit_code:
                    exit_code.append(0)

        window.events.loaded += on_loaded
        threading.Thread(target=worker, daemon=True).start()

        try:
            webview.start(gui='edgechromium')
        except Exception as error:
            log.warning(f'启动进度窗口事件循环异常：{error}')
        if exit_code:
            raise SystemExit(exit_code[0])


def _centered_position(width: int, height: int) -> tuple[int | None, int | None]:
    """算出窗口在主屏工作区居中的逻辑坐标。

    pywebview 自带的 CenterScreen 在无边框加阴影、且系统有缩放时会算偏，
    这里自己按主屏工作区算，算不出来时返回 None 交给 pywebview 兜底。
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ('left', wintypes.LONG),
                ('top', wintypes.LONG),
                ('right', wintypes.LONG),
                ('bottom', wintypes.LONG),
            ]

        # 取主屏工作区（已扣除任务栏）
        rect = RECT()
        SPI_GETWORKAREA = 0x0030
        if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return None, None

        # 系统缩放：窗口尺寸按逻辑像素创建，屏幕坐标按物理像素给出，需要换算
        dc = user32.GetDC(0)
        LOGPIXELSX = 88
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, LOGPIXELSX) if dc else 96
        if dc:
            user32.ReleaseDC(0, dc)
        scale = dpi / 96.0 if dpi else 1.0

        work_width = int((rect.right - rect.left) / scale)
        work_height = int((rect.bottom - rect.top) / scale)
        x = int(rect.left / scale) + (work_width - width) // 2
        y = int(rect.top / scale) + (work_height - height) // 2
        return x, y
    except Exception as error:
        log.warning(f'计算进度窗口居中位置失败，交由默认定位：{error}')
        return None, None


# 新窗口归属判定的缓存：pid -> 是不是本项目自己的 python 进程
_project_python_cache: dict[int, bool] = {}


def _is_project_python(pid: int) -> bool:
    """这个进程是不是本项目的 python。

    只看「新出现的窗口」还不够，用户在这几秒里开个资源管理器、浏览器也会是新的。
    主界面一定是 python 进程，而且工作目录跟启动器一致，用这两条把不相干的窗口挡掉。
    """
    if pid in _project_python_cache:
        return _project_python_cache[pid]
    ok = False
    try:
        import psutil

        proc = psutil.Process(pid)
        if proc.name().lower() in ('python.exe', 'pythonw.exe'):
            ok = Path(proc.cwd()).resolve() == Path.cwd().resolve()
    except Exception:
        ok = False
    _project_python_cache[pid] = ok
    return ok


def _foreign_window_handles() -> set[int]:
    """桌面上属于本项目 python 进程的可见大窗口。

    排除启动器自己进程的窗口，进度窗口和它的网页子窗口就不会被算进来。
    """
    if sys.platform != 'win32':
        return set()
    try:
        import ctypes

        user32 = ctypes.windll.user32
        my_pid = os.getpid()
        handles: set[int] = set()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _param):
            if not user32.IsWindowVisible(hwnd):
                return True
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == my_pid:
                return True
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            # 主界面是成块的窗口，借此排掉任务栏提示、工具提示这类小窗口
            if rect.right - rect.left < 200 or rect.bottom - rect.top < 150:
                return True
            if _is_project_python(owner.value):
                handles.add(hwnd)
            return True

        user32.EnumWindows(_enum, None)
        return handles
    except Exception as error:
        log.debug(f'枚举桌面窗口失败：{error}')
        return set()


def _wait_main_window(report: ProgressReporter) -> None:
    """等主界面窗口出现并画完第一帧，再让进度窗口收尾。

    判据是「拉起主界面前后，桌面上多出一个本项目 python 进程的大窗口」，
    不看窗口标题，也不需要主界面反过来说一声，换标题、换版本都不影响。
    """
    report(96, '正在打开主界面')

    baseline = _foreign_window_handles()
    deadline = time.time() + _MAIN_WINDOW_TIMEOUT
    window_seen_at: float | None = None
    while time.time() < deadline:
        if _foreign_window_handles() - baseline:
            now = time.time()
            if window_seen_at is None:
                window_seen_at = now
                log.info('已经看到主界面窗口，等它画完第一帧')
            elif now - window_seen_at >= _MAIN_WINDOW_GRACE:
                return
        time.sleep(_POLL_INTERVAL)

    log.warning('等待主界面窗口超时，结束启动进度窗口')


def _hide_console() -> None:
    """隐藏随 exe 启动的控制台窗口。

    打包后的启动器是控制台程序，黑框会一直挂在桌面上挡住进度窗口。
    这里只隐藏窗口、不改控制台模式，日志照常写入文件。
    源码运行时不动控制台，否则会连带把开发者的终端一起藏掉。
    """
    if not getattr(sys, 'frozen', False):
        return
    with contextlib.suppress(Exception):
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)
            return

        # 控制台属于父进程时 GetConsoleWindow 拿不到句柄，按窗口归属兜底查找
        pid = os.getpid()
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd_, _param):
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd_, ctypes.byref(window_pid))
            if window_pid.value == pid:
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd_, cls, 64)
                if cls.value == 'ConsoleWindowClass':
                    found.append(hwnd_)
                    return False
            return True

        user32.EnumWindows(_enum, None)
        for handle in found:
            user32.ShowWindow(handle, 0)


def _run_js(window, script: str) -> None:
    """在工作线程里把脚本推给页面，页面未就绪或已关闭时忽略。"""
    try:
        window.evaluate_js(script)
    except Exception as error:
        # 页面还没加载完时调用会失败，下一次进度会补上，但要留痕避免整条链路静默失效
        log.debug(f'写入启动进度失败：{error}')


def _close(window) -> None:
    """在独立线程里关闭窗口。

    destroy 需要 UI 线程空闲才能完成，调用线程本身不能被它阻塞，
    否则窗口关掉了、主线程却还在等。
    """
    def _destroy() -> None:
        with contextlib.suppress(Exception):
            window.destroy()

    thread = threading.Thread(target=_destroy, daemon=True)
    thread.start()
    thread.join(timeout=3)


def _noop_reporter(percent: int, text: str) -> None:
    """窗口不可用时的空进度上报。"""


def _noop_wait_main_window() -> None:
    """窗口不可用时不等待主界面。"""
