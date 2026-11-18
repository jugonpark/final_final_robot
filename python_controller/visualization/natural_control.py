"""The dashboard's Korean plan, approval, and distance-control panel."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from python_controller.visualization.korean import ko, movement_block


PHASE_TEXT = {
    "IDLE": "명령을 입력한 뒤 계획을 생성하세요.",
    "PLANNING": "NVIDIA에서 이동 계획을 생성하는 중입니다.",
    "READY": "계획 검증 완료. 방향과 거리를 확인하세요.",
    "EXECUTING": "장치 제어 중",
    "STOPPING": "정지를 요청했습니다. 송신 작업이 끝날 때까지 기다리세요.",
    "CANCELLING": "계획 요청을 취소했습니다. 응답은 폐기합니다.",
    "CANCELLED": "계획이 취소됐습니다.",
    "DONE": "목표 완료 후 ESP32의 정지 응답을 확인했습니다.",
    "FAILED": "요청을 완료하지 못했습니다.",
}
DETAIL_TEXT = {
    "HANDSHAKE": "장비의 정지 응답 확인 중",
    "MOVE_ACCEPTED": "거리 이동을 확인했습니다.",
    "GOAL_REACHED": "목표 도달 보고를 확인했습니다. 정지 응답 확인 중",
    "STOP_CONFIRMED": "ESP32가 정지를 보고했습니다.",
    "OPERATOR_STOP": "실행 중단. 실제 정지 여부는 장비에서 확인하세요.",
    "LLM_DEPENDENCIES_MISSING": "requirements-llm.txt의 패키지를 설치하세요.",
    "NVIDIA_API_KEY_MISSING": "로컬 .env의 NVIDIA_API_KEY를 확인하세요.",
    "NVIDIA_MODEL_MISSING": "로컬 .env의 NVIDIA_MODEL을 확인하세요.",
    "INPUT_LENGTH": "명령을 1~350자로 입력하세요.",
    "INVALID_ESP32_IP": "시리얼 STATUS에서 확인한 ESP32의 IPv4 주소를 입력하세요.",
    "MOVE_DISTANCE_LIMIT": "이동마다 5~50cm를 입력하세요.",
    "COMMAND_REQUIRES_CLARIFICATION": "명령의 방향과 거리를 명확히 입력하세요.",
    "INCONSISTENT_GOAL_COMPLETION": "목표 완료 텔레메트리에 모순이 있어 다음 이동을 차단했습니다.",
    "UDP_BIND_FAILED": "UDP 8889를 사용 중인 다른 제어기를 종료하세요.",
    "STOP_NOT_CONFIRMED": "장비의 정지 응답을 확인할 수 없습니다.",
    "SESSION_MISMATCH": "다른 제어 세션이 감지됐습니다. 다른 제어기를 종료하세요.",
}


def label(text=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    return widget


class NaturalControlPanel(QWidget):
    def __init__(self, engine, shared_host, emergency_stop):
        super().__init__()
        self.engine = engine
        self.control = engine.language
        self._displayed_plan = None
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        plan_box = QGroupBox("명령과 이동 계획")
        plan_layout = QVBoxLayout(plan_box)
        entry = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setPlaceholderText("예: 앞으로 5cm 이동해")
        self.command.setAccessibleName("한국어 이동 명령")
        self.command.textChanged.connect(self._input_changed)
        self.command.returnPressed.connect(self._plan)
        self.plan_button = QPushButton("계획 생성")
        self.plan_button.clicked.connect(self._plan)
        entry.addWidget(self.command, 1)
        entry.addWidget(self.plan_button)
        plan_layout.addLayout(entry)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("검증된 이동 순서")
        self.preview.setPlaceholderText("검증된 계획이 이동 순서대로 표시됩니다.")
        self.preview.setFixedHeight(116)
        plan_layout.addWidget(self.preview)
        layout.addWidget(plan_box)

        execution = QGroupBox("실제 장치 실행")
        actions = QVBoxLayout(execution)
        form = QFormLayout()
        self.host = QLineEdit(shared_host.text())
        self.host.setPlaceholderText("시리얼 STATUS에서 확인한 주소")
        self.host.setAccessibleName("ESP32 IPv4")
        self.host.textChanged.connect(shared_host.setText)
        shared_host.textChanged.connect(self.host.setText)
        self.host.textChanged.connect(self._reset_approval)
        form.addRow("ESP32 IPv4", self.host)
        if not engine.central_ui:
            actions.addLayout(form)
        else:
            self.host.hide()
        self.prepared = QCheckBox("바퀴 방향과 시험 공간을 확인했으며 이 세션의 실물 제어를 승인합니다")
        self.prepared.toggled.connect(self.refresh)
        actions.addWidget(self.prepared)
        buttons = QHBoxLayout()
        self.execute_button = QPushButton("계획 실행")
        self.execute_button.clicked.connect(self._execute)
        self.stop_button = QPushButton("정지 / 계획 취소")
        self.stop_button.setObjectName("estop")
        self.stop_button.clicked.connect(emergency_stop)
        buttons.addWidget(self.execute_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch()
        actions.addLayout(buttons)
        self.gate = label()
        actions.addWidget(self.gate)
        layout.addWidget(execution)

        feedback = QGroupBox("실행 상태")
        feedback_layout = QVBoxLayout(feedback)
        self.status = label()
        self.telemetry = label()
        self.session_status = label()
        self.log = label()
        for widget in (self.status, self.telemetry, self.session_status, self.log):
            widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
            feedback_layout.addWidget(widget)
        layout.addWidget(feedback)
        layout.addStretch()
        self.refresh()

    def _reset_approval(self):
        self.prepared.setChecked(False)
        self.refresh()

    def _input_changed(self):
        self.control.discard()
        self._reset_approval()

    def _plan(self):
        if self.control.busy or not self.command.text().strip():
            return
        try:
            self.control.start_plan(self.command.text())
        except ValueError as exc:
            self.status.setText(DETAIL_TEXT.get(str(exc), str(exc)))
            return
        self._reset_approval()

    def _execute(self):
        if not self.execute_button.isEnabled():
            return
        try:
            self.engine.start_natural_motion(self.host.text().strip(), self.prepared.isChecked())
        except ValueError as exc:
            self._reset_approval()
            self.status.setText(DETAIL_TEXT.get(str(exc), str(exc)))
            return
        self._reset_approval()

    def refresh(self):
        control = self.control
        if control.plan is not self._displayed_plan:
            self._displayed_plan = control.plan
            rows = []
            if control.plan is not None:
                for index, action in enumerate(control.plan.actions, 1):
                    step = f"{ko(action.direction.upper())}  {action.distance_cm:g} cm" if action.type == "move" else "정지"
                    rows.append(f"{index}.  {step}")
            self.preview.setPlainText("\n".join(rows))
        self.command.setEnabled(not control.busy)
        self.host.setEnabled(not control.executing)
        self.plan_button.setEnabled(not control.busy and bool(self.command.text().strip()))
        bench_busy = self.engine.client is not None
        preview = self.engine.source != "LIVE_CAMERA"
        can_prepare = control.phase == "READY" and not preview and not bench_busy
        authorized = self.host.text().strip() == self.engine.language_authorized_host
        shared = self.engine.shared_session
        transport = shared.snapshot() if shared else {}
        blocked = movement_block(transport)
        if self.engine.central_ui:
            can_prepare = (control.phase == 'READY' and not preview and shared is not None and
                           self.engine.client_owner == 'LLM_COMMAND' and not self.engine.pending_mode and
                           self.engine.release_future is None and transport.get('stop_verified') and
                           transport.get('ready') and not blocked)
            authorized = shared is not None
        self.prepared.setVisible(not authorized)
        if self.engine.central_ui:
            self.prepared.setVisible(False)
        self.prepared.setEnabled(can_prepare and not authorized)
        self.execute_button.setEnabled(can_prepare and (authorized or self.prepared.isChecked()) and bool(self.host.text().strip()))
        if bench_busy or preview:
            self.prepared.setChecked(False)
        if preview:
            gate = "합성 미리보기에서는 계획만 확인할 수 있습니다."
        elif self.engine.central_ui:
            gate = ('중앙 연결을 사용합니다. 계획을 확인하고 실행하세요.' if can_prepare else
                    '상단에서 ESP32를 연결하세요.' if shared is None else
                    '계획과 ESP32 정지 응답을 확인하는 중입니다.')
        elif bench_busy:
            gate = "모터 동작 시험 탭에서 UDP 연결을 먼저 해제하세요."
        elif authorized:
            gate = "이 세션은 실물 실행이 승인되었습니다. 각 계획은 실행 버튼을 눌러 시작합니다."
        else:
            gate = "계획 생성은 dry-run입니다. 첫 실물 실행 때 이 세션을 한 번 승인하세요."
        self.gate.setText(gate)
        if self.engine.central_ui and shared:
            self.gate.setText(f'{gate} 출력 정지 {"확인" if transport.get("stop_verified") else "미검증"} · '
                              f'구동 준비 {"가능" if transport.get("ready") and not blocked else "차단"} · '
                              f'{ko(blocked or transport.get("recovery_state") or "UNKNOWN")}')
        message = PHASE_TEXT[control.phase]
        if control.detail:
            message = f"{message}  {DETAIL_TEXT.get(control.detail, control.detail)}"
        if control.stop_result:
            if control.stop_result["stop_verified"]:
                message += "  새 STOP 응답 및 출력 정지 확인."
                if control.stop_result.get("fault") not in (None, "NONE"):
                    message += f'  장치 오류 유지 ({control.stop_result["fault"]}).'
            else:
                message += "  STOP 응답 미확인. 장비에서 정지 여부를 확인하세요."
        self.status.setText(message)
        data = control.last_feedback
        if data:
            fields = [f'최근 ESP32 응답 {ko(data["state"])}']
            fields.append(f'장치 Fault {ko(data.get("fault") or "UNKNOWN")}')
            if data.get("goal_progress_cm") is not None:
                fields.append(f'진행 {data["goal_progress_cm"]:g} cm')
            if data.get("remaining_cm") is not None:
                fields.append(f'남은 거리 {data["remaining_cm"]:g} cm')
            self.telemetry.setText(" · ".join(fields))
        else:
            self.telemetry.clear()
        self.telemetry.setVisible(bool(data))
        session = control.session_diagnostic
        if session:
            self.session_status.setText(
                f'{session["session_mismatch_reason"]} · 송신 세션 {session["tx_session_id"]} '
                f'/ 수신 세션 {session["rx_session_id"]} · 부팅 {session["boot_id"]} '
                f'· 송신 순번 {session["tx_seq"]} / 수신 순번 {session["last_seq"]}'
            )
        self.session_status.setVisible(bool(session))
        if self.engine.central_ui and shared:
            fault = (transport.get('telemetry') or {}).get('fault')
            self.session_status.setText(
                f'검증 상세 {ko(transport.get("validation_detail") or "UNKNOWN")} · '
                f'장치 Fault {ko(fault or "UNKNOWN")} · '
                f'복구 {ko(transport.get("recovery_state") or "UNKNOWN")} / {transport.get("recovery_generation", "미확인")}'
            )
            self.session_status.setVisible(True)
        self.log.setText(f"기록 {control.log_path}" if control.log_path else "")
        self.log.setVisible(control.log_path is not None)
