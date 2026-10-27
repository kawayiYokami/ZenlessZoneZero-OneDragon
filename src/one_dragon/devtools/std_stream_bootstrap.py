"""打包为无控制台程序时的标准流兜底。

窗口模式的 exe 没有控制台，``sys.stdout`` / ``sys.stderr`` 是 ``None``，
任何 ``print`` 都会直接抛异常。这里在最早时机把标准流接好：

- 被父进程以管道方式调用（如版本查询）时，直接接管已有的管道句柄；
- 双击运行等没有管道的情况，重定向到空设备，日志仍写文件。

这样既能彻底消除控制台黑框，也保证 ``--version`` 的输出仍能被读取。
"""

from __future__ import annotations

import os
import sys


def bootstrap_standard_streams() -> None:
    """确保 ``sys.stdout`` / ``sys.stderr`` 可用。"""
    if sys.stdout is not None and sys.stderr is not None:
        return

    for name, fd, mode in (('stdout', 1, 'w'), ('stderr', 2, 'w')):
        if getattr(sys, name) is not None:
            continue
        stream = _open_fd(fd, mode)
        if stream is None:
            # 空设备要一直开着供整个进程生命周期使用，不能交给 with 管理
            stream = open(os.devnull, mode, encoding='utf-8')  # noqa: SIM115
        setattr(sys, name, stream)


def _open_fd(fd: int, mode: str):
    """接管已存在的标准输出句柄；句柄无效时返回 None。"""
    try:
        # 管道或重定向场景下这个文件描述符是有效的
        return open(fd, mode, encoding='utf-8', errors='replace', closefd=False)
    except OSError:
        return None
