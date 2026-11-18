"""Operator setup and bounded tracking tuning for GESTURE_FOLLOW."""
from time import monotonic

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from python_controller.visualization.natural_control import label
from python_controller.visualization.korean import ko, movement_block


class FollowControlPanel(QWidget):
    def __init__(self, engine, shared_host, disconnect):
        super().__init__()
        self.engine = engine
        outer = QVBoxLayout(self)
        box = QGroupBox("사람 추종")
        layout = QVBoxLayout(box)
        form = QFormLayout()
        self.host = QLineEdit(shared_host.text())
        self.host.setPlaceholderText("ESP32의 현재 IPv4")
        self.host.textChanged.connect(shared_host.setText)
        shared_host.textChanged.connect(self.host.setText)
        form.addRow("ESP32 IPv4", self.host)
        if not engine.central_ui:
            layout.addLayout(form)
        else:
            self.host.hide()
        actions = QHBoxLayout()
        self.connect_button = QPushButton("추종 시작 / 재개")
        self.connect_button.clicked.connect(self.connect)
        self.disconnect_button = QPushButton("정지 후 연결 해제")
        self.disconnect_button.clicked.connect(disconnect)
        actions.addWidget(self.connect_button)
        actions.addWidget(self.disconnect_button)
        self.disconnect_button.setVisible(not engine.central_ui)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(label("카메라에서 대상을 선택하고 추종 시작을 누르세요. 손바닥은 재개, 주먹은 정지, 엄지 동작은 목표 거리를 조절합니다."))
        self.connection_status = label()
        self.connection_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.connection_status)
        outer.addWidget(box)

        self.settings = QGroupBox("추종 설정")
        controls = QFormLayout(self.settings)
        self.age = QSpinBox()
        self.age.setRange(250, 500)
        self.age.setSingleStep(50)
        self.age.setSuffix(" ms")
        self.age.setValue(round(engine.cfg["safety"]["perception_timeout_s"] * 1000))
        self.hold = QDoubleSpinBox()
        self.hold.setRange(.3, 1.5)
        self.hold.setSingleStep(.05)
        self.hold.setSuffix(" 초")
        self.hold.setValue(engine.cfg["gesture"]["hold_s"])
        self.frames = QSpinBox()
        self.frames.setRange(2, 8)
        self.frames.setValue(engine.cfg["gesture"]["min_frames"])
        self.height = QDoubleSpinBox()
        self.height.setRange(.85, .98)
        self.height.setSingleStep(.01)
        self.height.setValue(engine.cfg["follow"]["size_max"])
        self.image_size = QComboBox()
        for size in (320, 416, 480, 640):
            self.image_size.addItem(f"{size} px", size)
        selected = self.image_size.findData(engine.cfg["vision"]["image_size"])
        self.image_size.setCurrentIndex(selected if selected >= 0 else self.image_size.findData(480))
        controls.addRow("영상 유효시간", self.age)
        controls.addRow("제스처 유지시간", self.hold)
        controls.addRow("제스처 최소 프레임", self.frames)
        controls.addRow("사람 박스 최대 높이 비율", self.height)
        controls.addRow("YOLO 추론 해상도", self.image_size)
        buttons = QHBoxLayout()
        self.preset = QPushButton("느린 카메라 시험값 적용")
        self.preset.clicked.connect(self.set_preset)
        self.apply_button = QPushButton("설정 적용")
        self.apply_button.clicked.connect(self.apply)
        buttons.addWidget(self.preset)
        buttons.addWidget(self.apply_button)
        controls.addRow(buttons)
        self.status = label()
        controls.addRow(self.status)
        outer.addWidget(self.settings)
        outer.addStretch()
        self.refresh()

    def connect(self):
        try:
            if self.engine.central_ui:
                self.engine.resume_follow()
            else:
                self.engine.connect_gesture_follow(self.host.text().strip(), True, True, auto_arm=True)
        except (ValueError, OSError) as exc:
            self.connection_status.setText(str(exc))
            return
        self.connection_status.setText("STOP 응답을 기다리는 중입니다.")
        self.refresh()

    def set_preset(self):
        self.age.setValue(500)
        self.hold.setValue(.45)
        self.frames.setValue(2)
        self.height.setValue(.95)
        self.image_size.setCurrentIndex(self.image_size.findData(320))
        self.apply()

    def apply(self):
        try:
            self.engine.tune_tracking(self.age.value() / 1000, self.hold.value(),
                                      self.frames.value(), self.height.value(), self.image_size.currentData())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.status.setText("이번 실행에 적용했습니다. 목표와 제어 준비를 다시 확인하세요.")

    def refresh(self, transport=None):
        mode = self.engine.active_control_mode
        idle = mode == "SIMULATION"
        shared=self.engine.shared_session
        transport=transport if transport is not None else (shared.snapshot() if shared else {})
        blocked=movement_block(transport)
        if self.engine.central_ui:
            self.connect_button.setEnabled(idle or bool(shared and mode=='GESTURE_FOLLOW' and not self.engine.pending_mode and
                                               not self.engine.release_future and not self.engine.language.executing and
                                               not blocked and transport.get('ready')))
        else:
            self.connect_button.setEnabled(idle and self.engine.source == "LIVE_CAMERA")
        self.host.setEnabled(idle)
        self.disconnect_button.setEnabled(mode == "GESTURE_FOLLOW")
        self.settings.setEnabled(idle or bool(shared and not shared.active and not self.engine.pending_mode))
        diagnostic = (transport or {}).get("session_diagnostic")
        if mode == "GESTURE_FOLLOW" and diagnostic:
            self.connection_status.setText(
                f'{diagnostic["session_mismatch_reason"]} · 송신 세션 {diagnostic["tx_session_id"]} '
                f'/ 수신 세션 {diagnostic["rx_session_id"]} · 부팅 {diagnostic["boot_id"]}'
            )
        elif mode == "GESTURE_FOLLOW" and transport:
            requested = transport.get("stop_requested_at")
            if transport["stop_verified"]:
                status = "STOP 응답 확인"
            elif transport.get("armed"):
                status = "추종 제어 준비됨"
            elif requested is not None and monotonic() - requested >= 1:
                status = "STOP 응답 미확인. 장비의 실제 정지 여부를 확인하세요."
            else:
                status = "STOP 응답 대기 중"
            self.connection_status.setText(status)
        if shared:
            fault=(transport.get('telemetry') or {}).get('fault')
            self.connection_status.setText(
                f'출력 정지 {"확인" if transport.get("stop_verified") else "미검증"} · '
                f'구동 준비 {"가능" if transport.get("ready") and not blocked else "차단"} · '
                f'복구 {ko(transport.get("recovery_state") or "UNKNOWN")} · '
                f'검증 {ko(transport.get("validation_detail") or "UNKNOWN")} · '
                f'장치 Fault {ko(fault or "UNKNOWN")} · {ko(blocked or transport.get("reason") or "UNKNOWN")}'
            )
