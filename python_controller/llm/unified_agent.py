"""Host-side tools and live operator authorization for the conversation graph."""
import re
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from secrets import token_hex
from threading import Thread
from time import monotonic

from python_controller.llm.agent_graph import ConversationGraph, load_plan
from python_controller.llm.agent_intent import NvidiaIntentInterpreter, emergency_stop
from python_controller.planning.schema import describe_action
from python_controller.planning.validator import ValidatedPlan
from python_controller.visualization.korean import ko, movement_block

MODE_LABELS = {'LLM_COMMAND': 'AI 대화 제어', 'GESTURE_DIRECT': '손동작 직접 제어',
               'GESTURE_FOLLOW': '사람 추종', 'DIRECT_CONTROL': '수동 제어'}
MODE_WORDS = {'LLM_COMMAND': r'LLM|AI 대화(?: 제어)?|자연어(?: 제어)?',
              'GESTURE_DIRECT': r'손동작(?: 직접 제어)?', 'GESTURE_FOLLOW': r'사람 추종',
              'DIRECT_CONTROL': r'수동(?: 제어)?|직접 제어'}


def explicit_mode(text, mode):
    text = text.strip().rstrip('.!。！?').strip()
    if mode == 'GESTURE_FOLLOW' and re.fullmatch(r'사람(?:을)?\s*따라가고\s*싶어(?:요)?', text):
        return True
    return bool(re.fullmatch(rf'(?:{MODE_WORDS[mode]})\s*(?:으로|로)?\s*'
                             r'(?:바꿔(?:줘| 줘| 주세요)?|전환(?:해(?:줘| 줘| 주세요)?)?|조종할래|조종하고 싶어|제어할래)', text, re.I))


class RobotCommandFacade:
    def __init__(self, engine):
        self.engine = engine

    def live(self):
        e = self.engine
        session = e.shared_session
        tr = session.snapshot() if session else {}
        radar = tr.get('radar') or (e.radar.snapshot(monotonic()) if e.radar else None) or {}
        telemetry = tr.get('telemetry') or {}
        return session, tr, radar, telemetry

    def binding(self):
        e = self.engine
        session, tr, radar, telemetry = self.live()
        return (e.control_generation, session, session.protocol.session_id if session else None,
                telemetry.get('boot_id'), telemetry.get('session_id'), tr.get('recovery_generation'),
                radar.get('generation'), radar.get('state'), radar.get('blocked'), bool(tr.get('connected')),
                movement_block(tr) if session else None, e.client_owner, e.pending_mode,
                e.release_future is not None, e.c.estop_latched, tr.get('stop_requested_at'))

    def get_status(self, target='all'):
        e = self.engine
        _, tr, radar, telemetry = self.live()
        data, result = e.render_state()
        data = data or {}
        fresh = bool(result and data.get('perception_fresh') and
                     0 <= monotonic()-result['frame'].timestamp <= e.cfg['safety']['perception_timeout_s'])
        status = {
            'robot': f'제어 모드 {MODE_LABELS.get(e.active_control_mode, e.active_control_mode)} · '
                     f'ESP32 {"연결" if tr.get("connected") else "미연결"} · '
                     f'STOP {"확인" if tr.get("stop_verified") else "미확인"}',
            'radar': f'레이더 {radar.get("state", "UNKNOWN")} · '
                     f'{"이동 차단" if radar.get("blocked", True) else "차단 해제"} · '
                     f'{ko(radar.get("reason") or "RADAR_NO_DATA")}',
            'camera': f'카메라 {"합성 영상" if e.source != "LIVE_CAMERA" else "실카메라"} · '
                      f'{"인식 유효" if fresh else "인식 미확인"}',
            'execution': f'실행 상태 {e.language.phase} · '
                         f'정지/보류 {ko("EMERGENCY_STOP" if e.c.estop_latched else data.get("stop_reason") or "UNKNOWN")}',
        }
        if e.pending_mode:
            status['robot'] += f' · {MODE_LABELS.get(e.pending_mode, e.pending_mode)} 전환 대기'
        fault = telemetry.get('fault') or tr.get('fault')
        if fault and fault != 'NONE':
            status['robot'] += f' · 장치 오류 {ko(fault)}'
        return '\n'.join(status.values()) if target == 'all' else status[target]

    def request_mode_change(self, mode):
        e = self.engine
        if not e.shared_session:
            return 'ESP32 중앙 제어 연결 후에 제어 모드를 전환할 수 있습니다.'
        e.select_control_mode(mode, auto_arm=False)
        return (f'{MODE_LABELS[mode]} 전환 요청 · 새 STOP 응답 대기 중' if e.pending_mode else
                f'현재 제어 모드는 {MODE_LABELS[mode]}입니다.')

    def motion_session(self, *, check_idle=True):
        e = self.engine
        session, tr, radar, telemetry = self.live()
        if not (session and e.central_ui and e.source == 'LIVE_CAMERA' and e.client_owner == 'LLM_COMMAND' and
                tr.get('controller_owner') == 'LLM_COMMAND' and not e.pending_mode and not e.release_future and
                not e.c.estop_latched and not e.control_error and not e.error and
                (not check_idle or not e.language.busy) and
                tr.get('connected') and tr.get('ready') and tr.get('stop_verified') and not tr.get('distance_leased') and
                not movement_block(tr) and radar.get('state') == 'CLEAR' and radar.get('blocked') is False and
                telemetry.get('boot_id') and telemetry.get('session_id') == session.protocol.session_id):
            raise ValueError('LLM 제어권, 레이더 CLEAR와 차단 해제, 정상 연결 및 새 STOP 응답이 필요합니다.')
        return session


@dataclass(frozen=True)
class ApprovalTicket:
    nonce: str
    plan_id: str
    revision: int
    plan: ValidatedPlan
    binding: tuple
    expires_at: float


class UnifiedAgent:
    def __init__(self, engine, interpreter=None):
        self.engine = engine
        self.bridge = RobotCommandFacade(engine)
        self.graph = ConversationGraph(interpreter or NvidiaIntentInterpreter())
        self.events = SimpleQueue()
        self.messages = []
        self.busy = False
        self.card = self.card_binding = self.ticket = None
        self.navigation = None
        self.navigation_id = 0
        self.generation = 0
        self._seen_control_generation = engine.control_generation

    def _append(self, role, text):
        self.messages = [*self.messages[-39:], {'role': role, 'content': text}]

    def _invalidate(self):
        self.generation += 1
        self.card = self.card_binding = self.ticket = None
        self.navigation = None

    def _sync(self):
        if self._seen_control_generation != self.engine.control_generation:
            self._seen_control_generation = self.engine.control_generation
            self._invalidate()
        elif self.card and self.card_binding != self.bridge.binding():
            self._invalidate()
            self._append('assistant', '제어 또는 안전 상태가 바뀌어 대기 계획을 폐기했습니다.')

    def emergency(self):
        with self.engine.control_lock:
            self.engine.request('ESTOP')
            self._sync()
            self._append('assistant', '긴급 정지를 요청했습니다. 장치 STOP 응답은 별도로 확인합니다.')

    def acknowledge_estop(self):
        """A separate, local operator event. Never called by an interpreted intent."""
        with self.engine.control_lock:
            e = self.engine
            if not e.c.estop_latched:
                return
            session, tr, radar, _ = self.bridge.live()
            if e.release_future or e.pending_mode or e.language.busy or e.control_error or e.error:
                raise ValueError('정지 처리와 관제 상태를 확인하세요.')
            if e.client and (not session or movement_block(tr) or tr.get('distance_leased') or
                             not tr.get('ready') or not tr.get('stop_verified') or
                             radar.get('state') != 'CLEAR' or radar.get('blocked') is not False):
                raise ValueError('정상 연결, 레이더 차단 해제 및 새 STOP 응답을 확인하세요.')
            e.c.estop_latched = False
            e.c.halt('PAUSED', 'OPERATOR_REARM_REQUIRED', True)
            e.pending = None
            e.hand_armed_once = True
            e.follow_auto_arm = False
            e.control_generation += 1
            self._sync()
            self._append('assistant', '정지 상태를 확인했습니다. 제어를 재개하려면 새 입력이 필요합니다.')

    def _run(self, kind, value, operation):
        token, binding = self.generation, self.bridge.binding()
        self.busy = True

        def work():
            try:
                result = operation()
            except Exception as exc:
                code = str(exc)
                result = {'error': code if re.fullmatch(r'[A-Z][A-Z0-9_]{2,79}', code) else 'INVALID_AGENT_RESPONSE'}
            self.events.put((token, binding, kind, value, result))

        Thread(target=work, name=f'dig-agent-{kind}', daemon=True).start()

    def submit(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 350:
            raise ValueError('메시지를 1~350자로 입력하세요.')
        text = text.strip()
        with self.engine.control_lock:
            self._sync()
            cancel = re.fullmatch(r'(?:실행\s*|계획\s*)?취소(?:해\s*줘|해\s*주세요|해)?', text.rstrip('.!。！'))
            if emergency_stop(text) or (cancel and self.engine.language.executing):
                self._append('user', text)
                self.emergency()
                return
            if cancel:
                self._append('user', text)
                self._invalidate()
                self._append('assistant', '대기 계획을 취소했습니다.')
                return
            if self.busy:
                raise ValueError('이전 요청을 처리하고 있습니다.')
            # Only prior operator text is sent back. Live tool responses remain local.
            history = [item for item in self.messages if item['role'] == 'user'][-8:]
            previous = self.card
            self.ticket = None
            self._append('user', text)
            self._run('message', text, lambda: self.graph.turn(text, history, previous))

    def review(self, plan_id, revision, *, approve, operator_confirmed=False):
        with self.engine.control_lock:
            self._sync()
            card = self.card
            if self.busy or not card or card['id'] != plan_id or type(revision) is not int or card['revision'] != revision:
                raise ValueError('현재 표시된 계획을 다시 확인하세요.')
            if type(approve) is not bool:
                raise ValueError('승인 또는 취소를 선택하세요.')
            if not approve and card['rotation']:
                self._invalidate()
                self._append('assistant', '회전 미리보기를 취소했습니다.')
                return
            if approve:
                if operator_confirmed is not True or card['rotation']:
                    raise ValueError('운영자 승인과 검증된 이동 계획이 필요합니다.')
                self.bridge.motion_session()
                plan = load_plan(card['actions'])
                self.ticket = ApprovalTicket(token_hex(16), plan_id, revision, plan, self.card_binding, monotonic()+5)
            ticket = self.ticket
            decision = 'approve' if approve else 'reject'
            self._run('review', ticket, lambda: {'decision': self.graph.review(card, decision)})

    def _start_motion(self, ticket):
        current = self.ticket
        self.ticket = None
        card = self.card
        if not (ticket and current is ticket and card and card['id'] == ticket.plan_id and
                card['revision'] == ticket.revision and monotonic() < ticket.expires_at and
                ticket.binding == self.bridge.binding() and load_plan(card['actions']) == ticket.plan):
            raise ValueError('승인이 만료되었거나 관제 상태가 바뀌었습니다.')
        session = self.bridge.motion_session()
        self._invalidate()

        def acquire_distance_socket():
            with self.engine.control_lock, session.lock:
                if monotonic() >= ticket.expires_at or self.bridge.binding() != ticket.binding:
                    raise ValueError('AGENT_SESSION_CHANGED')
                if self.bridge.motion_session(check_idle=False) is not session:
                    raise ValueError('AGENT_SESSION_CHANGED')
                return session.distance_socket()

        self.engine.start_natural_motion(session.host, True, validated_plan=ticket.plan, session_guard=acquire_distance_socket)
        return '이동 실행을 요청했습니다. 실제 진행과 정지 응답은 실행 상태에서 확인하세요.'

    def _dispatch(self, intent, text):
        kind = intent['intent']
        if kind == 'navigate':
            self.navigation = intent['page']
            self.navigation_id += 1
            return '화면 이동을 요청했습니다.'
        if kind == 'query_status':
            return self.bridge.get_status(intent['target'])
        if kind == 'switch_mode':
            mode = intent['mode']
            if not explicit_mode(text, mode):
                return '원하는 제어 모드를 한 가지 지정해 주세요. 예: 수동 제어로 바꿔줘'
            reply = self.bridge.request_mode_change(mode)
            self._sync()
            return reply
        if kind in ('stop', 'cancel_plan'):
            return '정지하려면 “멈춰”, 대기 계획을 지우려면 “계획 취소”라고 입력하세요.'
        return intent['reply']

    def poll(self):
        with self.engine.control_lock:
            self._sync()
            try:
                token, binding, kind, value, result = self.events.get_nowait()
            except Empty:
                return
            self.busy = False
            if token != self.generation or binding != self.bridge.binding():
                self.ticket = None
                return
            try:
                if result.get('error'):
                    self._invalidate()
                    reply = f'요청을 확인하지 못했습니다. ({result["error"]})'
                elif kind == 'review':
                    if result['decision'] == 'approve':
                        reply = self._start_motion(value)
                    else:
                        self._invalidate()
                        reply = '계획을 취소했습니다.' if result['decision'] == 'reject' else '계획을 다시 생성해 주세요.'
                elif result.get('intent'):
                    reply = self._dispatch(result['intent'], value)
                elif result.get('card'):
                    self.card, self.card_binding = result['card'], binding
                    reply = '검증된 이동 계획을 확인해 주세요.'
                    if self.card['rotation']:
                        reply = '각도 제어 미검증으로 이 계획 전체는 미리보기만 가능합니다.'
                else:
                    raise ValueError('Agent가 사용할 수 있는 의도를 반환하지 않았습니다.')
            except ValueError as exc:
                self._invalidate()
                reply = str(exc)
            if reply:
                self._append('assistant', reply)

    def view(self):
        self.poll()
        with self.engine.control_lock:
            can_approve = False
            card = self.card
            if card and not card['rotation'] and not self.busy:
                try:
                    self.bridge.motion_session()
                    can_approve = True
                except ValueError:
                    pass
            return {'messages': self.messages, 'busy': self.busy, 'can_approve': can_approve,
                    'card': {**card, 'steps': [describe_action(a) for a in load_plan(card['actions']).actions]} if card else None,
                    'page': self.navigation, 'navigation_id': self.navigation_id,
                    'status': self.bridge.get_status(), 'execution': self.engine.language.phase,
                    'execution_detail': self.engine.language.detail,
                    'stop_result': self.engine.language.stop_result,
                    'estop_latched': self.engine.c.estop_latched,
                    'mode': self.engine.active_control_mode, 'pending_mode': self.engine.pending_mode}
