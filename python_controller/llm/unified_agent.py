"""Host-side tools and live operator authorization for the conversation graph."""
import re
from dataclasses import dataclass
from math import isfinite
from queue import Empty, SimpleQueue
from secrets import token_hex
from threading import Thread
from time import monotonic

from python_controller.execution.history_store import HistoryUnavailable
from python_controller.llm.agent_graph import ConversationGraph, load_plan
from python_controller.llm.agent_intent import NvidiaIntentInterpreter, corrected_mode, emergency_stop, explicit_mode, program_question
from python_controller.planning.schema import describe_action
from python_controller.planning.validator import ValidatedPlan
from python_controller.visualization.korean import ko, movement_block

MODE_LABELS = {'LLM_COMMAND': 'AI 대화 제어', 'GESTURE_DIRECT': '손동작 직접 제어',
               'GESTURE_FOLLOW': '사람 추종', 'DIRECT_CONTROL': '수동 제어'}
STEP_LABELS = {'WAITING': '대기', 'MOVING': '이동 수락 · 진행 중',
               'GOAL_REPORTED': '목표 도달 보고 · STOP 확인 중', 'STOP_VERIFIED': 'STOP 확인 · 단계 완료',
               'BLOCKED': '실행 중단'}
RUN_LABELS = {'EXECUTING': '실행 진행 중', 'STOPPING': '정지 요청 · 응답 대기',
              'COMPLETED': '모든 단계 STOP 확인 · 완료', 'ABORTED': '실행 중단'}


def progress_summary(progress):
    if not progress:
        return '실행 기록이 없습니다.'
    verified, steps = progress['verified_count'], progress['steps']
    status = f'계획 v{progress["revision"]} · {RUN_LABELS[progress["state"]]}'
    if steps:
        status += f' · 단계 STOP 확인 {verified}/{len(steps)}'
    if progress['current_step']:
        index = progress['current_step']
        status += f'\n{index}단계 {progress["labels"][index-1]} · {STEP_LABELS[steps[index-1]]}'
    if progress['last_error']:
        status += '\n' + ko(progress['last_error'])
    return status


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
        camera = (data.get('pipeline_health') or {}).get('camera')
        status = {
            'robot': f'제어 모드 {MODE_LABELS.get(e.active_control_mode, e.active_control_mode)} · '
                     f'ESP32 {"연결" if tr.get("connected") else "미연결"} · '
                     f'STOP {"확인" if tr.get("stop_verified") else "미확인"}',
            'radar': f'레이더 {radar.get("state", "UNKNOWN")} · '
                     f'{"이동 차단" if radar.get("blocked", True) else "차단 해제"} · '
                     f'{ko(radar.get("reason") or "RADAR_NO_DATA")}',
            'camera': '카메라 꺼짐' if camera == 'CAMERA_OFF' else
                      f'카메라 {"합성 영상" if e.source != "LIVE_CAMERA" else "실카메라"} · '
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

    def explain_blocked(self, target, card):
        e = self.engine
        session, tr, radar, _ = self.live()
        reasons = []
        if not session or not tr.get('connected'):
            reasons.append('ESP32 중앙 제어 통신 미확인')
        if radar.get('state') != 'CLEAR' or radar.get('blocked') is not False:
            reasons.append(f'레이더 이동 차단 · {ko(radar.get("reason") or "RADAR_NO_DATA")}')
        if e.c.estop_latched:
            reasons.append('긴급 정지 상태')
        if not tr.get('stop_verified'):
            reasons.append('장치 STOP 응답 미확인')
        if session and (blocked := movement_block(tr)):
            reasons.append(ko(blocked))
        if e.pending_mode or e.client_owner != 'LLM_COMMAND':
            reasons.append('LLM 제어권 전환 확인 필요')
        if session and not tr.get('ready'):
            reasons.append('장치 준비 미확인')
        if e.release_future or e.control_error or e.error:
            reasons.append('중앙 제어 종료 또는 오류 상태 확인 필요')
        if e.language.executing or tr.get('distance_leased'):
            reasons.append('이전 실행 종료 및 STOP 확인 필요')
        elif not card:
            reasons.append('승인 대기 중인 이동 계획 없음')
        elif card['rotation']:
            reasons.append('각도 제어 미검증 · 전체 계획 실행 차단')
        else:
            reasons.append('현재 버전의 Qt 운영자 승인 필요')
        suffix = '\n'.join(f'• {reason}' for reason in dict.fromkeys(reasons))
        return f'{self.get_status(target)}\n{suffix}'

    def vision_facts(self):
        e = self.engine
        data, result = e.render_state()
        mode = e.active_control_mode
        mapping = {'GESTURE_DIRECT': e.hand.mapping, 'GESTURE_FOLLOW': e.cfg['gesture']['mapping']}.get(mode, {})
        fresh = bool(e.source == 'LIVE_CAMERA' and data and result and data.get('perception_fresh') and
                     0 <= monotonic() - result['frame'].timestamp <= e.cfg['safety']['perception_timeout_s'])
        camera = ((data or {}).get('pipeline_health') or {}).get('camera')
        hands, people = [], None
        if fresh:
            for hand in data.get('vision', {}).get('raw', []):
                score = hand.get('confidence')
                if isinstance(score, (int, float)) and isfinite(score) and 0 <= score <= 1:
                    hands.append({'name': hand['name'], 'confidence': score})
            if not result.get('hand_only') and not (data.get('pipeline_health') or {}).get('tracking_error'):
                people = data.get('detected_people')
        return {'source': e.source, 'fresh': fresh, 'people': people, 'hands': hands, 'mapping': dict(mapping),
                'mode': mode, 'camera_status': camera}

    def describe_vision(self, target):
        facts = self.vision_facts()
        lines = []
        if target != 'mapping':
            if facts['camera_status'] == 'CAMERA_OFF':
                lines.append('카메라가 꺼져 있습니다. 카메라 · 손동작 화면에서 켤 수 있습니다.')
            elif facts['source'] != 'LIVE_CAMERA':
                lines.append('합성 영상 미리보기입니다. 실제 사람이나 손동작 검출 결과는 없습니다.')
            elif not facts['fresh']:
                lines.append('최근 유효한 카메라 인식 결과가 없어 사람이나 손동작을 확인할 수 없습니다.')
            else:
                if target in ('all', 'people'):
                    people = facts['people']
                    lines.append('현재 사람 검출 결과를 사용할 수 없습니다.' if people is None else f'검출 인원 {people}명')
                if target in ('all', 'hands'):
                    hands = [f'{ko(hand["name"])} {hand["confidence"]:.0%}' for hand in facts['hands']]
                    lines.append('손동작 · ' + ' / '.join(hands) if hands else '분류된 손동작이 없습니다.')
        if target in ('all', 'mapping'):
            mode = MODE_LABELS.get(facts['mode'], facts['mode'])
            mapping = [f'{ko(gesture)} → {ko(action)}' for gesture, action in facts['mapping'].items()]
            lines.append(f'{mode} · ' + (' / '.join(mapping) if mapping else '적용 중인 제스처 매핑 없음'))
        return '\n'.join(lines)

    def mode_preview_state(self):
        session, tr, radar, telemetry = self.live()
        e = self.engine
        if (not session or not e.central_ui or e.source != 'LIVE_CAMERA' or e.c.estop_latched or
                e.release_future or e.error or e.control_error or movement_block(tr) or
                radar.get('state') != 'CLEAR' or radar.get('blocked') is not False or
                not telemetry.get('boot_id') or telemetry.get('session_id') != session.protocol.session_id):
            raise ValueError('정상 연결, 레이더 차단 해제 및 긴급 정지 상태를 확인하세요.')
        return tr, (session, session.protocol.session_id, telemetry['boot_id'], tr['recovery_generation'],
                    radar.get('generation'), tr.get('stop_requested_at'))

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


@dataclass(frozen=True)
class PendingModePlan:
    motion: str
    identity: tuple
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
        self.mode_plan = None
        self.navigation = None
        self.navigation_id = 0
        self.generation = 0
        self._seen_control_generation = engine.control_generation

    def _append(self, role, text):
        self.messages = [*self.messages[-39:], {'role': role, 'content': text}]

    def _invalidate(self):
        self.generation += 1
        self.card = self.card_binding = self.ticket = None
        self.mode_plan = None
        self.navigation = None

    def clear_conversation(self):
        with self.engine.control_lock:
            self._invalidate()
            self.graph = ConversationGraph(self.graph.interpreter)
            self.events = SimpleQueue()
            self.messages = []
            self.busy = False

    def _sync(self):
        if self._seen_control_generation != self.engine.control_generation:
            self._seen_control_generation = self.engine.control_generation
            if self.mode_plan:
                self._append('assistant', '제어 상태가 바뀌어 전환 후 계획 요청을 취소했습니다.')
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
        events = self.events
        self.busy = True

        def work():
            try:
                result = operation()
            except Exception as exc:
                code = str(exc)
                result = {'error': code if re.fullmatch(r'[A-Z][A-Z0-9_]{2,79}', code) else 'INVALID_AGENT_RESPONSE'}
            events.put((token, binding, kind, value, result))

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
            if program_question(text):
                self._append('user', text)
                self._append('assistant', self._describe_program())
                return
            if self.busy or self.mode_plan or self.engine.language.executing:
                reply = self._read_during_operation(text)
                if reply is not None:
                    self._append('user', text)
                    self._append('assistant', reply)
                    return
            if self.busy:
                raise ValueError('이전 요청을 처리하고 있습니다.')
            if self.mode_plan:
                self._invalidate()
                if mode := corrected_mode(text):
                    self._append('user', text)
                    reply = self.bridge.request_mode_change(mode)
                    self._sync()
                    self._append('assistant', f'전환 후 계획 요청을 취소했습니다. {reply}')
                    return
                self._append('assistant', '새 요청으로 전환 후 계획 요청을 취소했습니다.')
            # Only prior operator text is sent back. Live tool responses remain local.
            history = [item for item in self.messages if item['role'] == 'user'][-8:]
            previous = self.card
            self.ticket = None
            self._append('user', text)
            graph = self.graph
            self._run('message', text, lambda: self._interpret(graph, text, history, previous))

    def _interpret(self, graph, text, history, previous):
        result = graph.turn(text, history, previous)
        intent = result.get('intent') or {}
        if intent.get('intent') != 'query_history':
            return result
        failures = intent['target'] == 'last_failure'
        store = self.engine.language.history
        try:
            rows = store.recent(failures=failures)
            if not rows:
                reply = '확인할 수 있는 중단 이력이 없습니다.' if failures else '저장된 실행 이력이 없습니다.'
            elif failures:
                run = store.detail(rows[0]['run_id'])
                terminal = run['events'][-1]
                step = f'{terminal["step_index"]}단계 · ' if terminal['step_index'] is not None else ''
                stop = next((event['evidence'].get('stop_verified') for event in reversed(run['events'])
                             if event['event_type'] == 'ABORT_STOP_RESULT'), None)
                proof = '장치 STOP 응답 확인' if stop else '장치 STOP 응답 미확인'
                provenance = '' if run['provenance'] == 'ESP32_EXECUTOR' else '시뮬레이션 기록 · '
                reply = f'{provenance}최근 중단 · 계획 v{run["revision"]} · {step}{ko(run["reason"])} · {proof}'
            else:
                states = {'COMPLETED': 'STOP 확인 후 완료', 'ABORTED': '중단', 'UNCONFIRMED': '종료 미확인'}
                reply = '\n'.join(f'{"시뮬레이션 · " if row["provenance"] != "ESP32_EXECUTOR" else ""}'
                                  f'계획 v{row["revision"]} · {states[row["state"]]} · {row["started_at"]}' for row in rows[:5])
        except HistoryUnavailable:
            reply = '실행 이력 저장소를 지금 읽을 수 없습니다.'
        if store.degraded:
            reply += '\n저장 오류로 일부 이력이 누락됐을 수 있습니다.'
        return {**result, 'intent': {'intent': 'chat', 'reply': reply}}

    def _read_during_operation(self, text):
        text = text.rstrip('.!?。！？').strip()
        if re.fullmatch(r'몇 단계 (?:진행됐어|남았어)|(?:실행 )?진행 (?:상태 )?알려\s*줘', text):
            return progress_summary(self.engine.language.progress_snapshot())
        if re.fullmatch(r'(?:지금 )?(?:관제 |로봇 )?상태 (?:알려|보여)\s*줘', text):
            return self.bridge.get_status()
        if re.fullmatch(r'왜 (?:안 움직여|멈췄어|실행이 안 돼)', text):
            return self.bridge.explain_blocked('all', self.card)
        for target, pattern in (('people', r'사람(?:이)? 보여|검출 인원 알려\s*줘'),
                                ('hands', r'손(?:동작| 모양)(?:이)? 보여'),
                                ('mapping', r'손바닥(?:을)? 펼치면 뭐\s*해|제스처 매핑 알려\s*줘')):
            if re.fullmatch(pattern, text):
                return self.bridge.describe_vision(target)
        return None

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
            graph = self.graph
            self._run('review', ticket, lambda: {'decision': graph.review(card, decision)})

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

        self.engine.start_natural_motion(session.host, True, validated_plan=ticket.plan,
                                         session_guard=acquire_distance_socket, plan_ref=(ticket.plan_id, ticket.revision))
        return '이동 실행을 요청했습니다. 실제 진행과 정지 응답은 실행 상태에서 확인하세요.'

    def _request_mode_plan(self, motion):
        self.bridge.mode_preview_state()
        self.bridge.request_mode_change('LLM_COMMAND')
        self._sync()
        self._invalidate()
        _, identity = self.bridge.mode_preview_state()
        self.mode_plan = PendingModePlan(motion, identity, monotonic()+8)
        return 'LLM 제어 모드와 STOP 응답을 확인한 뒤 이동 계획을 준비하겠습니다.'

    def _poll_mode_plan(self):
        pending = self.mode_plan
        if not pending:
            return
        try:
            tr, identity = self.bridge.mode_preview_state()
            if pending.identity != identity or monotonic() >= pending.expires_at:
                raise ValueError('전환을 확인할 수 없거나 대기 시간이 지났습니다.')
            if self.engine.pending_mode == 'LLM_COMMAND' or not tr.get('stop_verified'):
                return
            self.bridge.motion_session()
            binding = self.bridge.binding()
            result = self.graph.turn(pending.motion, [], None, preview=True)
            if result.get('error') or binding != self.bridge.binding():
                raise ValueError('현재 제어 상태와 이동 구문을 다시 확인하세요.')
            self.card, self.card_binding = result['card'], binding
            self._announce_card()
        except ValueError as exc:
            self._append('assistant', f'전환 후 계획 요청을 취소했습니다. {exc}')
        self.mode_plan = None

    def _announce_card(self):
        edit = f'계획을 v{self.card["revision"]}로 수정했습니다. 수정 전후를 확인해 주세요. ' if self.card.get('before') else ''
        self._append('assistant', edit + ('각도 제어 미검증으로 전체 계획은 미리보기만 가능합니다.' if self.card['rotation'] else
                     '실물 실행은 Qt에서 현재 버전을 승인해야 합니다.'))

    @staticmethod
    def _describe_program():
        return ('D.I.G.는 3WD 로봇의 상태를 확인하고 이동 계획을 대화로 만드는 미션 관제 프로그램이에요.\n\n'
                '• 미션 컨트롤: 레이더·연결 상태를 묻고, 이동 계획을 만들거나 수정할 수 있어요. 2D 화면에서는 예상 경로를 재생해요.\n'
                '• 카메라 · 손동작: 영상을 켜서 손동작 인식과 사람 추종 상태를 확인해요.\n'
                '• 작업 이력: 이동 단계별 결과와 STOP 응답, 중단 이유를 살펴볼 수 있어요.\n\n'
                '웹에서는 계획을 미리 보고, 실물 이동은 안전 조건이 확인된 뒤 Qt 관제에서 승인해요. '
                '“앞으로 10cm 가고 오른쪽으로 5cm 가”처럼 요청해 보세요.')

    def _dispatch(self, intent, text):
        kind = intent['intent']
        if kind == 'explain_program':
            return self._describe_program()
        if kind == 'navigate':
            self.navigation = intent['page']
            self.navigation_id += 1
            return '화면 이동을 요청했습니다.'
        if kind == 'query_status':
            return self.bridge.get_status(intent['target'])
        if kind == 'explain_blocked':
            return self.bridge.explain_blocked(intent['target'], self.card)
        if kind == 'query_vision':
            return self.bridge.describe_vision(intent['target'])
        if kind == 'query_progress':
            return progress_summary(self.engine.language.progress_snapshot())
        if kind == 'mode_then_plan':
            return self._request_mode_plan(intent['motion_text'])
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
            self._poll_mode_plan()
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
                    if kind != 'message' or not result.get('card'):
                        self._invalidate()
                    reply = ('어느 단계에서 무엇을 바꿀지 다시 알려 주세요. 예를 들어 “마지막 거리를 10cm로”라고 말할 수 있습니다.'
                             if result['error'] in ('EDIT_NOT_EXPLICIT', 'ACTION_INDEX_MISMATCH')
                             else f'요청을 확인하지 못했습니다. ({result["error"]})')
                elif kind == 'review':
                    if result['decision'] == 'approve':
                        reply = self._start_motion(value)
                    else:
                        self._invalidate()
                        reply = '계획을 취소했습니다.' if result['decision'] == 'reject' else '계획을 다시 생성해 주세요.'
                elif result.get('intent'):
                    reply = self._dispatch(result['intent'], value)
                elif result.get('card'):
                    card = result['card']
                    if card.get('before') and (not self.card or self.card['id'] != card['id'] or
                                               self.card['revision'] != card['revision'] - 1):
                        raise ValueError('현재 계획 버전에서 다시 요청해 주세요.')
                    self.card, self.card_binding = card, binding
                    self._announce_card()
                    reply = None
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
                    'history_degraded': self.engine.language.history.degraded,
                    'progress': self.engine.language.progress_snapshot(), 'vision_facts': self.bridge.vision_facts(),
                    'waiting_mode_plan': self.mode_plan is not None,
                    'estop_latched': self.engine.c.estop_latched,
                    'mode': self.engine.active_control_mode, 'pending_mode': self.engine.pending_mode}
