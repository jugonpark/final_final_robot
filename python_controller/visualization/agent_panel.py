"""Operator chat and explicit review for the blue Qt console."""
import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit,
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
        self.sources_toggle = QPushButton('관련 근거 보기 ▾')
        self.sources_toggle.setCheckable(True)
        self.sources_box = QWidget()
        source_layout = QVBoxLayout(self.sources_box)
        self.source_choice = QComboBox()
        self.source_text = QPlainTextEdit()
        self.source_text.setReadOnly(True)
        self.source_text.setMaximumHeight(180)
        source_layout.addWidget(self.source_choice)
        source_layout.addWidget(self.source_text)
        self.sources_box.hide()
        self.sources_toggle.toggled.connect(self.sources_box.setVisible)
        self.sources_toggle.toggled.connect(lambda opened: self.show_source() if opened else None)
        self.source_choice.currentIndexChanged.connect(self.show_source)
        chat_layout.addWidget(self.sources_toggle)
        chat_layout.addWidget(self.sources_box)
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
        self.request_review = button_style(QPushButton('현재 버전 실행 요청'), 'secondary')
        self.request_review.clicked.connect(self.request_execution)
        review_layout.addWidget(self.request_review)
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
        choice = QMessageBox.question(self, '대화 기록 삭제', '대화와 실행 요청을 지울까요? 저장된 초안은 보존됩니다.',
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if choice == QMessageBox.Yes:
            self.agent.clear_conversation()
            self.command.clear()
            self.feedback.clear()
            self.refresh()

    def request_execution(self):
        try:
            self.agent.submit('이 계획들 실행해')
            self.feedback.clear()
        except ValueError as exc:
            self.feedback.setText(str(exc))
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

    def show_source(self, *_):
        if not self.sources_toggle.isChecked():
            return
        selected = self.source_choice.currentData()
        if not selected:
            self.source_text.clear()
            return
        citation, related = selected
        try:
            row = self.agent.knowledge.get_document_chunk(citation['id'])
            body = row['body'] if row else '이 출처를 더 이상 열 수 없습니다.'
        except (OSError, ValueError, sqlite3.Error):
            body = '출처를 지금 열 수 없습니다.'
        self.source_text.setPlainText('\n'.join([
            f'{citation["path"]} · {citation["heading"]}',
            f'리비전 {citation.get("revision")} · {citation.get("evidence_status")}',
            '관련 주장: ' + ' / '.join(related),
            '사용한 인용: ' + '\n'.join(citation.get('excerpts',[])), '', body]))

    def refresh(self):
        state = self.agent.view()
        if state['navigation_id'] != self.last_navigation:
            self.last_navigation = state['navigation_id']
            if state['page']:
                self.navigate(state['page'])
        messages = state['messages']
        if state.get('partial_reply'):
            messages = [*messages, {'role': 'assistant', 'content': state['partial_reply']}]
        if messages != self.displayed_messages:
            self.displayed_messages = messages
            verification_labels = {'verified':'근거 검증 통과','partial':'일부만 확인','structured':'구조화 자료 기준',
                                   'unavailable':'검증 사용 불가','failed':'검증 실패 · 답변 보류',
                                   'not_verified':'설명 확인 안 됨','citation_only':'요청한 원문','conflicting':'자료 간 충돌',
                                   'policy_disabled':'현재 설정에서 AI 해석 미사용','restricted':'문서 외부 전송 제한',
                                   'runtime_snapshot':'현재 관제 스냅숏'}
            self.history.setPlainText('\n\n'.join(
                f'{"나" if row["role"] == "user" else "D.I.G."} · {row["content"]}' +
                (f'\n({verification_labels.get(row.get("verification_status"),"검증 상태 확인 필요")})' if row.get('answer_mode') else '')
                for row in messages))
            self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())
            self.sources_toggle.setChecked(False)
            self.source_choice.clear()
            for message in messages:
                for citation in message.get('citations',[]):
                    related = [c['text'] for c in message.get('claims',[]) if citation['id'] in c['evidence_ids']]
                    self.source_choice.addItem(f'{citation["path"]} · {citation["heading"]}',(citation,related))
            self.sources_toggle.setEnabled(self.source_choice.count()>0)
        card = state['card']
        self.clear_button.setEnabled(bool(state['messages'] or state['busy'] or (card and card['review_requested'])))
        shown = (card['id'], card['revision']) if card else None
        self.can_approve = state['can_approve']
        if self.displayed_card != shown or not self.can_approve:
            self.prepared.setChecked(False)
        self.displayed_card = shown
        if card:
            summary = 'Qt 운영자 승인 대기' if card['review_requested'] else '미실행 초안'
            lines = [f'{summary} · v{card["revision"]}', *(f'{i}. {step}' for i, step in enumerate(card['steps'], 1))]
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
        self.request_review.setEnabled(bool(card and not card['rotation'] and not card['review_requested'] and not state['busy']
                                            and state['execution'] not in ('EXECUTING', 'STOPPING')))
        self.reject.setEnabled(bool(card and card['review_requested'] and not state['busy']))
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
