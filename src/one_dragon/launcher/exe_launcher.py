import argparse
import os
import sys

import pyuac

from one_dragon.launcher.launcher_base import LauncherBase

# 调试/测试用：置 1 时跳过管理员提权，直接用当前权限启动
_SKIP_UAC_ENV = 'ONE_DRAGON_SKIP_UAC'


def _skip_uac() -> bool:
    """是否跳过管理员提权。只用于本机调试，正常启动不受影响。"""
    return os.environ.get(_SKIP_UAC_ENV) == '1'


class ExeLauncher(LauncherBase):
    """EXE启动器基类"""

    def __init__(self, description: str, version: str):
        LauncherBase.__init__(self, description)
        self.version = version

    def add_custom_arguments(self, parser: argparse.ArgumentParser) -> None:
        """添加自定义参数"""
        parser.add_argument("-v", "--version", action="store_true", help="显示版本号")
        parser.add_argument("-o", "--onedragon", action="store_true", help="一条龙运行")

    def show_version(self) -> None:
        """显示版本信息"""
        # 版本号会被其他程序读取，固定 UTF-8 写出；不改控制台代码页。
        sys.stdout.reconfigure(encoding='utf-8')
        print(f"{self.description} {self.version}")
        sys.exit(0)

    def build_launch_args(self, args) -> list[str]:
        """构建启动参数列表"""
        launch_args = []
        if args.instance:
            launch_args.extend(["--instance", args.instance])
        if args.close_game:
            launch_args.append("--close-game")
        if args.shutdown:
            launch_args.extend(["--shutdown", str(args.shutdown)])
        if args.hibernate:
            launch_args.append("--hibernate")
        if args.sleep:
            launch_args.append("--sleep")

        return launch_args

    def run_onedragon_mode(self, launch_args) -> None:
        """运行一条龙模式，子类实现"""
        pass

    def run_gui_mode(self) -> None:
        """运行GUI模式，子类实现"""
        pass

    def main(self, args) -> None:
        """执行主要逻辑"""
        if args.version:
            self.show_version()

        if not args.onedragon and (args.close_game or args.shutdown or args.hibernate or args.sleep or args.instance):
            print("错误：参数 --close-game, --shutdown, --hibernate, --sleep, --instance 只能在指定 --onedragon 时使用")
            sys.exit(1)

        # 一条龙模式和界面模式都需要管理员权限：界面里运行的自动化要注入鼠标键盘输入，
        # 而进程权限在启动时定死，只能在这一刻提权。提权后的进程再走更新同步与拉起主界面。
        if not pyuac.isUserAdmin() and not _skip_uac():
            try:
                pyuac.runAsAdmin(sys.argv, wait=False)
            except Exception:
                # 用户在 UAC 弹窗上点了取消 这不是错误 不必把异常栈甩给用户
                print("已取消管理员权限申请，启动中止")
                sys.exit(1)
            sys.exit(0)

        if args.onedragon:
            launch_args = self.build_launch_args(args)
            self.run_onedragon_mode(launch_args)
            return

        self.run_gui_mode()
