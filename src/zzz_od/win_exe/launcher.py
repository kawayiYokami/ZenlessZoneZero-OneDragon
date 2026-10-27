from one_dragon.devtools import python_launcher
from one_dragon.devtools.std_stream_bootstrap import bootstrap_standard_streams
from one_dragon.launcher.exe_launcher import ExeLauncher
from one_dragon.version import __version__

# 窗口模式打包后没有控制台，先把标准流接好，后续 print 才不会崩
bootstrap_standard_streams()


class ZLauncher(ExeLauncher):
    """绝区零启动器"""

    def __init__(self):
        ExeLauncher.__init__(self, "绝区零 一条龙 启动器", __version__)

    def run_onedragon_mode(self, launch_args) -> None:
        python_launcher.run_python(["zzz_od", "application", "zzz_application_launcher"], no_windows=False, args=launch_args, piped=True)

    def run_gui_mode(self) -> None:
        # 只有界面模式带启动进度窗口，其他入口保持命令行原样
        python_launcher.run_python(["zzz_od", "gui", "app"], no_windows=True, show_splash=True)


if __name__ == '__main__':
    launcher = ZLauncher()
    launcher.run()
