"""Capture real Qt widgets using a socket-blocked synthetic preview.

Run from the repository root: .venv/Scripts/python.exe scripts/capture_blue_ui.py
No device, camera, LLM or network connection is made.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QImage, QColor
from PySide6.QtWidgets import QApplication, QWidget, QGridLayout, QLabel, QPushButton, QStyle, QStyleOptionButton
from python_controller.config import load_config
from python_controller.dashboard_runtime import DashboardEngine, PreviewStream
from python_controller.planning.validator import ValidatedPlan, parse_operator_command
from python_controller.visualization.dashboard import Dashboard
from python_controller.visualization.dig_theme import button_style, stylesheet


class StateButton(QPushButton):
    """Render Qt's actual stylesheet with explicit interaction-state options."""
    def __init__(self, text, state):
        super().__init__(text)
        self.capture_state = state

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.state &= ~(QStyle.State_MouseOver | QStyle.State_Sunken | QStyle.State_HasFocus)
        if self.capture_state == 2:option.state |= QStyle.State_MouseOver
        if self.capture_state == 3:option.state |= QStyle.State_Sunken
        painter = QPainter(self)
        self.style().drawControl(QStyle.CE_PushButton, option, painter, self)


def capture_buttons(app, output):
    gallery = QWidget()
    gallery.setStyleSheet(stylesheet())
    grid = QGridLayout(gallery)
    grid.setContentsMargins(24,24,24,24)
    grid.setSpacing(16)
    for col, text in enumerate(('종류','기본','Hover','Pressed','Disabled')):
        grid.addWidget(QLabel(text), 0, col)
    for row, (variant, svg) in enumerate((('primary','send'),('secondary','play'),('danger','stop'),('navigation','chat')), 1):
        grid.addWidget(QLabel(variant), row, 0)
        for col in range(1, 5):
            button = StateButton('비상 정지' if variant=='danger' else '계획 생성' if variant=='primary' else '상세 보기', col)
            button_style(button, variant, svg)
            button.setMinimumWidth(180)
            if col==4:button.setEnabled(False)
            grid.addWidget(button,row,col)
    gallery.show()
    app.processEvents()
    gallery.grab().save(str(output/'button_states.png'))
    gallery.close()


def capture_overviews(output):
    names = ('ai_chat','camera_gestures','person_follow','manual','diagnostics','settings')
    for width,height in ((940,680),(1320,920),(1600,980)):
        sheet = QImage(1500,840,QImage.Format_RGB32)
        sheet.fill(QColor('#071324'))
        painter = QPainter(sheet)
        for index,name in enumerate(names):
            capture = QImage(str(output/f'{name}_{width}x{height}.png'))
            reduced = capture.scaled(480,380,Qt.KeepAspectRatio,Qt.SmoothTransformation)
            x,y=(index%3)*500+10,(index//3)*420+28
            painter.drawImage(x,y,reduced)
            painter.setPen(QColor('#EAF3FF'))
            painter.drawText(x,y-8,f'{name} · {width}×{height}')
        painter.end()
        sheet.save(str(output/f'overview_{width}x{height}.png'))


def main():
    app = QApplication.instance() or QApplication([])
    output = Path('docs/verification/blue_ui_verification')
    output.mkdir(parents=True, exist_ok=True)
    captures = []
    with tempfile.TemporaryDirectory() as folder, patch('socket.socket', side_effect=AssertionError('Preview opened a socket')):
        config = load_config()
        # Avoid exposing the operator's saved addresses in shareable screenshots.
        config['communication']['host'] = '192.0.2.10'
        config['radar']['source_ip'] = '192.0.2.20'
        engine = DashboardEngine(config, folder, 'SYNTHETIC_PREVIEW', logger=False)
        window = Dashboard(config, preview=True, engine=engine, stream=PreviewStream())
        window.timer.stop()
        window.show()
        try:
            for width, height in ((940, 680), (1320, 920), (1600, 980)):
                window.resize(width, height)
                for page, name in enumerate(('ai_chat', 'camera_gestures', 'manual', 'diagnostics', 'settings')):
                    window.navigate(page)
                    window.poll()
                    app.processEvents()
                    path = output / f'{name}_{width}x{height}.png'
                    if not window.grab().save(str(path)):
                        raise RuntimeError(f'Capture failed: {path}')
                    captures.append(dict(file=path.name, size=[window.width(), window.height()],
                                         estop=window.estop.isVisible(), radar=window.radar_status.isVisible()))
                window.navigate_camera(1)
                window.poll()
                app.processEvents()
                window.grab().save(str(output / f'person_follow_{width}x{height}.png'))
            window.resize(1320, 920)
            window.navigate(0)
            panel = window.language_panel
            panel.command.setText('앞으로 5cm 가고 뒤로 5cm 이동해')
            engine.language.plan = ValidatedPlan(parse_operator_command(panel.command.text()))
            engine.language.phase = 'READY'
            window.poll()
            app.processEvents()
            window.grab().save(str(output / 'ai_validated_plan.png'))
            panel.command.setText('좌로 한바퀴 회전해')
            engine.language.plan = ValidatedPlan(parse_operator_command(panel.command.text(), allow_rotation_preview=True))
            engine.language.phase = 'READY'
            window.poll()
            app.processEvents()
            window.grab().save(str(output / 'ai_rotation_blocked.png'))
            # Display-only injected snapshots, explicitly recorded as synthetic.
            for state in ('UNKNOWN', 'EVADE', 'CLEAR'):
                data = dict(window.last_data)
                data['transport'] = dict(data['transport'], stop_verified=False)
                data['radar'] = dict(state=state, blocked=True, reason='RADAR_'+state, age_s=None)
                data['transport']['radar'] = data['radar']
                window.update_display(data, None)
                app.processEvents()
                window.grab().save(str(output / f'radar_{state.lower()}_blocked.png'))
            window.estop.click()
            window.poll()
            app.processEvents()
            window.grab().save(str(output / 'emergency_stop.png'))
            capture_buttons(app, output)
            capture_overviews(output)
            (output / 'captures.json').write_text(json.dumps(dict(source='SYNTHETIC_PREVIEW',
                network='socket creation prohibited', radar_scenarios='display-only injected snapshots',
                hardware='NOT TESTED', captures=captures), ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            window.close()
    print(f'Qt captures saved to {output.resolve()}')


if __name__ == '__main__':
    main()
