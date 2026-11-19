"""Operator chat and explicit review for the blue Qt console."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QGroupBox, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from python_controller.llm.unified_agent import STEP_LABELS, progress_summary
from python_controller.visualization.dig_theme import button_style
from python_controller.visualization.natural_control import DETAIL_TEXT, PHASE_TEXT, label
from python_controller.visualization.korean import ko


class AgentPanel(QWidget):
    def __init__(self, agent, navigate):
        super().__init__()
        self.agent = agent
        self.navigate = navigate
        self.last_navigation = 0
        self.displayed_card = None
        self.displayed_messages = None
        layout = QVBoxLayout(self)
        heading = label('D.I.G. 통합 AI Agent')
        heading.setProperty('role', 'heroTitle')
        layout.addWidget(heading)
        layout.addWidget(label('관제 상태 조회, 화면 이동, 제어 모드 요청과 이동 계획을 한 대화에서 처리합니다.'))
        self.columns = QSplitter(Qt.Horizontal)
        self.columns.setChildrenCollapsible(False)
        layout.addWidget(self.columns)

        chat = QGroupBox('AI 대화')
        chat_layout = QVBoxLayout(chat)
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setPlaceholderText('예: 우리 프로그램을 좀 설명해 줄래?')
        self.history.setAccessibleName('Agent 대화 기록')
        self.history.setMinimumHeight(220)
        chat_layout.addWidget(self.history)
        entry = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setMaxLength(350)
        self.command.setPlaceholderText('예: 카메라 화면 보여줘')
        self.command.setAccessibleName('Agent 메시지')
        self.command.returnPressed.connect(self.send)
        self.send_button = button_style(QPushButton('전송'), 'primary', 'send')
        self.send_button.clicked.connect(self.send)
        self.clear_button = button_style(QPushButton('대화 삭제'), 'secondary')
        self.clear_button.clicked.connect(self.clear_conversation)
        entry.addWidget(self.command, 1)
        entry.addWidget(self.send_button)
        entry.addWidget(self.clear_button)
        chat_layout.addLayout(entry)
        self.feedback = label()
        chat_layout.addWidget(self.feedback)
        self.columns.addWidget(chat)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        review = QGroupBox('실행 계획 · 운영자 승인')
        review_layout = QVBoxLayout(review)
        self.preview = label('대기 중인 이동 계획이 없습니다.')
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        review_layout.addWidget(self.preview)
        self.prepared = QCheckBox('이 계획과 실물 이동 조건을 확인했습니다')
        self.prepared.toggled.connect(lambda *_: self._update_approve())
        review_layout.addWidget(self.prepared)
        self.approve = button_style(QPushButton('승인 및 실행'), 'primary', 'play')
        self.approve.clicked.connect(lambda: self.review(True))
        self.reject = button_style(QPushButton('계획 취소'), 'secondary')
        self.reject.clicked.connect(lambda: self.review(False))
        actions = QHBoxLayout()
        actions.addWidget(self.reject)
        actions.addWidget(self.approve)
        review_layout.addLayout(actions)
        right_layout.addWidget(review)
        safety = QGroupBox('안전 · 실행 상태')
        safety_layout = QVBoxLayout(safety)
        self.status = label()
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        safety_layout.addWidget(self.status)
        self.execution_progress = label()
        self.execution_progress.setTextInteractionFlags(Qt.TextSelectableByMouse)
        safety_layout.addWidget(self.execution_progress)
        self.ack_stop = button_style(QPushButton('긴급 정지 확인 · 새 요청 허용'), 'secondary', 'shield')
        self.ack_stop.clicked.connect(self.acknowledge_stop)
        safety_layout.addWidget(self.ack_stop)
        right_layout.addWidget(safety)
        right_layout.addStretch()
        self.columns.addWidget(right)
        self.columns.setSizes([600, 390])
        self.can_approve = False
        self.refresh()

    def send(self):
        text = self.command.text().strip()
        if not text:
            return
        try:
            self.prepared.setChecked(False)
            self.agent.submit(text)
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self.command.clear()
        self.feedback.clear()
        self.refresh()

    def clear_conversation(self):
        choice = QMessageBox.question(self, '대화 기록 삭제', '현재 대화의 메시지와 승인 대기 계획을 삭제할까요?',
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if choice == QMessageBox.Yes:
            self.agent.clear_conversation()
            self.command.clear()
            self.feedback.clear()
            self.refresh()

    def review(self, approve):
        if not self.displayed_card or (approve and not self.approve.isEnabled()):
            return
        try:
            self.agent.review(*self.displayed_card, approve=approve, operator_confirmed=self.prepared.isChecked())
            self.feedback.clear()
        except ValueError as exc:
            self.feedback.setText(str(exc))
        self.prepared.setChecked(False)
        self.refresh()

    def _update_approve(self):
        self.approve.setEnabled(self.can_approve and self.prepared.isChecked())

    def acknowledge_stop(self):
        try:
            self.agent.acknowledge_estop()
            self.feedback.clear()
        except ValueError as exc:
            self.feedback.setText(str(exc))
        self.refresh()

    def refresh(self):
        state = self.agent.view()
        if state['navigation_id'] != self.last_navigation:
            self.last_navigation = state['navigation_id']
            if state['page']:
                self.navigate(state['page'])
        if state['messages'] != self.displayed_messages:
            self.displayed_messages = state['messages']
            self.history.setPlainText('\n\n'.join(f'{"나" if row["role"] == "user" else "D.I.G."} · {row["content"]}' for row in state['messages']))
            self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())
        card = state['card']
        self.clear_button.setEnabled(bool(state['messages'] or card or state['busy']))
        shown = (card['id'], card['revision']) if card else None
        self.can_approve = state['can_approve']
        if self.displayed_card != shown or not self.can_approve:
            self.prepared.setChecked(False)
        self.displayed_card = shown
        if card:
            lines = [f'계획 버전 {card["revision"]}', *(f'{i}. {step}' for i, step in enumerate(card['steps'], 1))]
            if card.get('before'):
                lines.extend(['', f'수정 전 v{card["revision"] - 1}',
                              *(f'{i}. {step}' for i, step in enumerate(card['before'], 1))])
            if card['rotation']:
                lines.append('각도 제어 미검증 · 전체 실행 차단')
            self.preview.setText('\n'.join(lines))
        elif state['waiting_mode_plan']:
            self.preview.setText('제어 모드 전환 · 새 STOP 응답 대기')
        else:
            self.preview.setText('대기 중인 이동 계획이 없습니다.')
        self.prepared.setEnabled(self.can_approve)
        self.reject.setEnabled(bool(card and not state['busy']))
        self._update_approve()
        self.send_button.setText('처리 중 · 정지 가능' if state['busy'] else '전송')
        phase = PHASE_TEXT.get(state['execution'], state['execution'])
        detail = state['execution_detail']
        execution = f'{phase} {DETAIL_TEXT.get(detail, ko(detail)) if detail else ""}'.strip()
        if state['stop_result']:
            execution += '\n' + ('장치 STOP 응답 확인' if state['stop_result']['stop_verified'] else '장치 STOP 응답 미확인')
        if state['history_degraded']:
            execution += '\n실행 이력 저장 오류 · 일부 기록이 빠졌을 수 있습니다.'
        self.ack_stop.setVisible(state['estop_latched'])
        self.status.setText(f'{state["status"]}\n\n{execution}')
        progress = state['progress']
        self.execution_progress.setVisible(bool(progress))
        if progress:
            lines = [progress_summary(progress).split('\n', 1)[0]]
            lines.extend(f'{i}. {name} · {STEP_LABELS[status]}' for i, (name, status) in
                         enumerate(zip(progress['labels'], progress['steps']), 1))
            if progress['last_error']:
                lines.append(ko(progress['last_error']))
            self.execution_progress.setText('\n'.join(lines))
