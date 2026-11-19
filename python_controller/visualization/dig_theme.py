"""D.I.G. presentation resources. No device or control dependencies."""
from pathlib import Path
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

ROOT = Path(__file__).resolve().parent


def icon(name):
    return QIcon(str(ROOT / 'assets' / 'icons' / f'{name}.svg'))


def button_style(button, variant='secondary', icon_name=None):
    button.setProperty('variant', variant)
    if icon_name:
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(20, 20))
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def stylesheet():
    return (ROOT / 'dig_blue.qss').read_text(encoding='utf-8')
