"""贝果开发工具共用的浅色界面处理。

这两个窗口里的样式按浅色写死，需要两层一起改才能真的是白底深字：
- Qt 调色板：普通 QDialog 的背景与文字取自它，系统深色模式下默认是深底浅字。
- 全局主题：qfluentwidgets 的按钮、输入框按全局主题绘制，只改调色板管不到它们。

``setTheme`` 不会修改 Qt 调色板，所以两层都要处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QColor, QPalette
from qfluentwidgets import Theme, qconfig, setTheme

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


def apply_light_palette(widget: QWidget) -> None:
    """把窗口的调色板固定为白底深字。

    Args:
        widget: 需要应用浅色配色的窗口。
    """
    palette = widget.palette()
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Button,
        QPalette.ColorRole.AlternateBase,
        QPalette.ColorRole.ToolTipBase,
        QPalette.ColorRole.Base,
    ):
        palette.setColor(role, QColor('#ffffff'))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.ToolTipText,
    ):
        palette.setColor(role, QColor('#202020'))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor('#9a9a9a'))
    palette.setColor(QPalette.ColorRole.Highlight, QColor('#0f6cbd'))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor('#ffffff'))
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)


def enter_light_theme() -> Theme:
    """切到浅色主题并返回原主题，供关闭时还原。

    Returns:
        切换前的主题。
    """
    previous: Theme = qconfig.theme
    if previous != Theme.LIGHT:
        setTheme(Theme.LIGHT)
    return previous


def restore_theme(previous: Theme) -> None:
    """还原进入前的主题。

    Args:
        previous: ``enter_light_theme`` 返回的原主题。
    """
    if previous != Theme.LIGHT:
        setTheme(previous)
