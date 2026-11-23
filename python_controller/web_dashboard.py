"""Local web console for simulation or an explicitly shared Qt session."""
import base64
import binascii
import json
import re
import sqlite3
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_hex
from threading import Thread
from time import monotonic
from urllib.parse import parse_qs

from python_controller.communication.motor_test import load_connection
from python_controller.dashboard_runtime import DashboardEngine, PreviewStream, VisionStream
from python_controller.execution.history_store import HistoryUnavailable
from python_controller.knowledge.library import KnowledgeError, MAX_DOCUMENT
from python_controller.mission.draft_store import DraftConflict, DraftOperation
from python_controller.planning.dashboard_command import normalize_dashboard_command
from python_controller.planning.schema import describe_action
from python_controller.planning.validator import ValidatedPlan, parse_operator_command, strict_json_object
from python_controller.visualization.korean import ko, movement_block

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'web'
ICONS = ROOT / 'visualization' / 'assets' / 'icons'
CAMERA_VIEW_TIMEOUT_S = 3.0
WEB_VISION_LEASE_S = 1.0


class WebDashboard:
    def __init__(self, engine, stream_factory, agent=None, *, shared_stream=None, settings_dir=ROOT.parent / 'settings'):
        self.engine = engine
        self.stream_factory = stream_factory
        self.stream = shared_stream
        self.closing_stream = None
        self.shared = shared_stream is not None
        self.settings_dir = settings_dir
        self.manual_hold = None
        self.camera_views = {}
        self.camera_started_at = 0
        self.plan = None
        self.agent = agent

    def _stop_camera(self):
        self.closing_stream, self.stream = self.stream, None
        self.closing_stream.stop()
        engine = self.engine
        engine.last_result = None
        if engine.pending and engine.pending[0] in ('ARM', 'RESUME', 'SELECT', 'CLEAR', 'CONFIRM'):
            engine.pending = None
        engine.c.select_target(None)
        engine.c.halt('PAUSED', 'EMERGENCY_STOP' if engine.c.estop_latched else 'CAMERA_OFF', disarm=True)

    def update_camera(self):
        """Reconcile browser requests under the same lock as the next perception tick."""
        now = monotonic()
        self.camera_views = {viewer: expires for viewer, expires in self.camera_views.items() if expires > now}
        if self.stream is not None and not self.camera_views:
            self._stop_camera()
        if self.closing_stream is not None and self.closing_stream.stopped:
            self.closing_stream = None
        if self.camera_views and self.stream is None and self.closing_stream is None:
            self.stream = self.stream_factory()
            self.camera_started_at = now
            self.stream.start()
        return self.stream

    def close_camera(self):
        self.stop_camera_views()
        if self.shared:
            return
        for stream in (self.stream, self.closing_stream):
            if stream is not None:
                stream.close()

    def stop_camera_views(self):
        with self.engine.control_lock:
            self.camera_views.clear()
            if not self.shared and self.stream is not None:
                self._stop_camera()

    def state(self):
        with self.engine.control_lock:
            data, result = self.engine.render_state()
            data = data or {}
            tr = data.get('transport', {})
            radar = tr.get('radar') or data.get('radar') or {'state': 'UNKNOWN', 'blocked': True, 'reason': 'RADAR_NO_DATA'}
            estop = self.engine.c.estop_latched
            code = 'EMERGENCY_STOP' if estop else data.get('stop_reason', 'STARTING')
            stream = self.stream
            has_result = stream is not None and result is not None
            if stream is None and code == 'CAMERA_FAILED_OR_STALE':
                code = 'CAMERA_OFF'
            camera_status = (getattr(stream, 'camera_status', 'CAPTURING') if stream else
                             'CLOSING_CAMERA' if self.closing_stream else 'CAMERA_OFF')
            cfg = self.engine.cfg
            blocker = movement_block(tr)
            movement_reason = ko(blocker) if blocker else None
            if radar['blocked'] and tr.get('recovery_state') == 'SESSION_CONFLICT':
                movement_reason += ' · ' + ko('SESSION_CONFLICT')
            prepared = bool(tr.get('connected') and tr.get('ready') and tr.get('stop_verified') and
                            not blocker and not estop and not self.engine.control_error)
            return {
                'source': self.engine.source, 'owner': self.engine.active_control_mode,
                'shared_control': self.shared, 'session_open': bool(self.engine.client or self.engine.release_future),
                'movement_reason': movement_reason, 'movement_ready': prepared,
                'radar_can_rearm': bool(self.shared and self.engine.shared_session and radar['blocked'] and
                                        radar['state'] == 'CLEAR' and tr.get('stop_verified') and not self.engine.language.executing),
                'state': ko(data.get('state', 'STARTING')), 'stop_code': code, 'stop_reason': ko(code),
                'connected': bool(tr.get('connected')), 'ready': bool(tr.get('ready')),
                'stop_verified': bool(tr.get('stop_verified')), 'estop_latched': estop,
                'radar': {**radar, 'detail': ko(radar['reason'])},
                'following': has_result and bool(data.get('following')) and not estop,
                'armed': self.engine.c.armed, 'target': self.engine.c.target_id,
                'people': data.get('detected_people') if has_result else None,
                'fps': data.get('fps') if has_result else None, 'processing_ms': data.get('processing_ms') if has_result else None,
                'camera_status': camera_status, 'vision_status': ko(stream.status if stream else camera_status),
                'perception_fresh': has_result and bool(data.get('perception_fresh')),
                'vision': data.get('vision', {}) if has_result else {},
                'gesture': data.get('gesture', {}) if has_result else {}, 'safe': data.get('safe'), 'motors': data.get('motors', {}),
                'validation': tr.get('validation_detail'), 'recovery': tr.get('recovery_state'),
                'hardware_profile_status': ko(tr.get('hardware_profile_status')),
                'fault': tr.get('fault'), 'notice': self.engine.control_error or data.get('notice'),
                'tests': self.engine.book.rows, 'plan': self.plan,
                'history_degraded': self.engine.language.history.degraded,
                'agent': self.agent.view() if self.agent else None,
                'settings': {'esp32': cfg['communication']['host'], 'radar': cfg['radar']['source_ip'],
                             'hardware_profile': cfg['communication']['hardware_profile'],
                             'hand': self.engine.hand.mapping, 'follow': cfg['gesture']['mapping'],
                             'timeout_ms': round(1000*cfg['safety']['perception_timeout_s']),
                             'hold_s': cfg['gesture']['hold_s'], 'camera_index': cfg['camera']['index'],
                             'image_size': cfg['vision']['image_size']},
            }

    def camera(self, viewer=None):
        with self.engine.control_lock:
            now = monotonic()
            if viewer is not None:
                if self.camera_views.get(viewer, 0) <= now:
                    return {'image': None, 'status': '카메라 꺼짐', 'people': [], 'lease_active': False}
                self.camera_views[viewer] = now + CAMERA_VIEW_TIMEOUT_S
                lease = self.engine.web_vision
                if lease and lease[0] == viewer and now < lease[3]:
                    self.engine.web_vision = (*lease[:3], now + WEB_VISION_LEASE_S)
            stream = self.stream
            if stream is None:
                status = '카메라 연결 해제 중' if self.closing_stream else '카메라 꺼짐'
                return {'image': None, 'status': status, 'people': []}
            state, result = self.engine.render_state()
            capture = stream.latest_capture() if hasattr(stream, 'latest_capture') else None
        now = monotonic()
        timeout = self.engine.cfg['safety']['perception_timeout_s']
        if getattr(stream, 'camera_status', None) == 'CAMERA_ERROR':
            return {'image': None, 'status': '카메라 오류', 'people': []}
        frame = result['frame'] if result else None
        age = now-frame.timestamp if frame else float('inf')
        if frame and frame.camera_ok and 0 <= age <= max(1, timeout) and not stream.error:
            image = result['image']
            fresh = age <= timeout and bool(state and state['perception_fresh'])
            status = '인식 영상' if fresh else '인식 결과 지연 · 대상 선택 불가'
            token = frame.timestamp if fresh else None
            people = [{'id': p.track_id, 'bbox': [v / (frame.width if i % 2 == 0 else frame.height) for i, v in enumerate(p.bbox)]}
                      for p in frame.people] if fresh else []
            hands = [[[x/frame.width, y/frame.height] for x, y in points] for points, _ in result['hands']] if fresh else []
        elif capture and 0 <= now-capture[1] <= timeout:
            image = capture[0]
            status, token, people = '원본 영상 · 인식 결과 대기', None, []
            hands = []
        else:
            return {'image': None, 'status': '새 카메라 프레임 대기 중', 'people': []}
        import cv2
        ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 76])
        if not ok:
            raise ValueError('카메라 프레임을 표시할 수 없습니다.')
        with self.engine.control_lock:
            if stream is not self.stream or (viewer is not None and viewer not in self.camera_views):
                return {'image': None, 'status': '카메라 꺼짐', 'people': []}
        return {'image': 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii'),
                'status': status, 'frame': token, 'people': people, 'hands': hands,
                'aspect': image.shape[1]/image.shape[0]}

    def command(self, body):
        if not isinstance(body, dict):
            raise ValueError('명령 형식을 확인하세요.')
        action = body.get('action')
        engine = self.engine
        with engine.control_lock:
            if action == 'estop':
                if self.agent:
                    self.agent.emergency()
                else:
                    engine.request('ESTOP')
                self.plan = None
                return {'message': '비상 정지를 요청했습니다.'}
            if action == 'agent_clear_conversation' and self.agent:
                self.agent.clear_conversation()
                return {'message': '대화 기록을 삭제했습니다.'}
            if action in ('save_esp32_ip', 'save_radar_ip'):
                if action == 'save_esp32_ip':
                    engine.save_esp32_host(self.settings_dir / 'connection.json', body.get('address'))
                else:
                    engine.save_radar_source(self.settings_dir / 'radar.json', body.get('address'))
                return {'message': engine.notice}
            if self.shared and action in ('disconnect_robot', 'manual_release'):
                return self._shared_command(action, body)
            unavailable = engine.control_error or (not self.shared and (engine.client or engine.language.executing))
            if action == 'camera_power':
                viewer, enabled = body.get('viewer'), body.get('enabled')
                if not isinstance(viewer, str) or not re.fullmatch('[0-9a-f]{32}', viewer) or type(enabled) is not bool:
                    raise ValueError('카메라 요청 형식을 확인하세요.')
                if enabled:
                    if unavailable:
                        raise ValueError('시뮬레이션 상태에서만 요청할 수 있습니다.')
                    now = monotonic()
                    self.camera_views = {key: expires for key, expires in self.camera_views.items() if expires > now}
                    self.camera_views[viewer] = now + CAMERA_VIEW_TIMEOUT_S
                else:
                    self.camera_views.pop(viewer, None)
                    if self.shared and engine.web_vision and engine.web_vision[0] == viewer:
                        engine.request('PAUSE')
                if self.shared:
                    return {'message': '공유 카메라 보기를 갱신했습니다.'}
                if unavailable:
                    self.stop_camera_views()
                else:
                    self.update_camera()
                return {'message': '카메라를 요청했습니다.' if enabled else '카메라 보기를 종료했습니다.'}
            if unavailable:
                raise ValueError('현재 관제 상태에서는 요청할 수 없습니다.')
            if self.shared and action in ('connect_robot', 'select_mode', 'radar_rearm',
                                           'manual_prepare', 'manual_hold', 'acknowledge_estop',
                                           'agent_approve', 'activate_hand', 'recover_robot', 'test_turn'):
                return self._shared_command(action, body)
            if self.agent and action == 'agent_message':
                self.agent.submit(body.get('text'))
                return {'message': '메시지를 받았습니다.'}
            if self.agent and action == 'agent_reject':
                self.agent.review(body.get('plan_id'), body.get('revision'), approve=False)
                return {'message': '계획 취소를 요청했습니다.'}
            if action == 'plan':
                self.plan = None
                command = body.get('text')
                if not isinstance(command, str) or not 0 < len(command.strip()) <= 350:
                    raise ValueError('명령을 1~350자로 입력하세요.')
                try:
                    plan = ValidatedPlan(parse_operator_command(normalize_dashboard_command(command), allow_rotation_preview=True))
                except ValueError as exc:
                    raise ValueError('명령을 확인해 주세요. (' + str(exc) + ')') from exc
                self.plan = {'text': command, 'steps': [describe_action(a) for a in plan.actions],
                             'actions': [action.model_dump(mode='json') for action in plan.actions],
                             'distance_cm': format(sum(a.distance_cm for a in plan.actions if a.type == 'move'), 'f'),
                             'rotation': plan.requires_angle_control}
                return {'message': '이동 방향과 거리를 확인하세요.'}
            if action == 'discard_plan':
                self.plan = None
                return {'message': '계획을 지웠습니다.'}
            if action == 'pause':
                engine.request('PAUSE')
            elif action == 'clear_target':
                if self.shared and engine.client:
                    engine.request('PAUSE')
                engine.request('CLEAR')
            elif action in ('select_target', 'resume_follow'):
                self._follow(action, body)
            else:
                raise ValueError('이 명령은 웹 관제 1차에서 지원하지 않습니다.')
        return {'message': '관제에 요청을 전달했습니다.' if self.shared else '시뮬레이션 제어 요청을 전달했습니다.'}

    def _shared_command(self, action, body):
        engine = self.engine
        if action == 'connect_robot':
            if body.get('confirmed') is not True:
                raise ValueError('ESP32 통신 연결을 확인하세요.')
            engine.connect_robot(engine.cfg['communication']['host'])
        elif action == 'disconnect_robot':
            engine.disconnect_async()
        elif action == 'select_mode':
            engine.select_control_mode(body.get('mode'), auto_arm=False)
        elif action == 'radar_rearm':
            engine.rearm_radar()
        elif action == 'recover_robot':
            engine.recover_robot()
        elif action == 'test_turn':
            if (body.get('confirmed') is not True or engine.c.estop_latched or not engine.shared_session or
                    engine.client_owner != 'MOTOR_TEST'):
                raise ValueError('바퀴를 띄워 고정하고 바퀴 시험 제어권을 확인하세요.')
            engine.begin_hold('TURN_LEFT', timed_trial=True)
        elif action == 'activate_hand':
            self._check_vision_operator(body, 'GESTURE_DIRECT')
            hand = engine.hand
            engine.configure_hand(confidence=hand.confidence, frames=hand.min_frames, hold=hand.hold, mapping=hand.mapping)
            self._lease_vision(body['viewer'])
        elif action == 'acknowledge_estop' and self.agent:
            self.agent.acknowledge_estop()
        elif action == 'agent_approve' and self.agent:
            self.agent.review(body.get('plan_id'), body.get('revision'), approve=True,
                              operator_confirmed=body.get('confirmed') is True)
        elif action in ('manual_prepare', 'manual_hold', 'manual_release'):
            return self._manual_command(action, body)
        else:
            raise ValueError('현재 공유 관제에서 사용할 수 없는 명령입니다.')
        return {'message': '중앙관제에 요청을 전달했습니다.'}

    def _check_vision_operator(self, body, owner):
        engine = self.engine
        viewer = body.get('viewer')
        if (not isinstance(viewer, str) or self.camera_views.get(viewer, 0) <= monotonic() or
                body.get('confirmed') is not True or not engine.shared_session or engine.client_owner != owner or
                engine.c.estop_latched or engine.pending_mode or engine.release_future):
            raise ValueError('카메라 보기, 제어권과 운영자 확인이 필요합니다.')
        snapshot = engine.shared_session.snapshot()
        state, _ = engine.render_state()
        if (not state or not state.get('perception_fresh') or not snapshot['ready'] or
                not snapshot['stop_verified'] or movement_block(snapshot)):
            raise ValueError('최신 카메라 영상, 이동 차단 사유와 ESP32 STOP 응답을 확인하세요.')

    def _lease_vision(self, viewer):
        self.engine.web_vision = (viewer, self.engine.shared_session, self.engine.client_owner,
                                  monotonic() + WEB_VISION_LEASE_S)

    def _manual_command(self, action, body):
        engine = self.engine
        session = engine.shared_session
        if action == 'manual_prepare':
            direction = body.get('direction')
            owner = engine.client_owner
            direct = owner == 'DIRECT_CONTROL' and direction in ('FORWARD', 'BACKWARD', 'LEFT', 'RIGHT', 'TURN_LEFT', 'TURN_RIGHT')
            wheel = owner == 'MOTOR_TEST' and direction in ('M1', 'M2', 'M3') and body.get('confirmed') is True
            if not session or not (direct or wheel) or engine.c.estop_latched:
                raise ValueError('수동 제어권과 ESP32 정지 상태를 확인하세요.')
            session.release_web_hold()
            token = token_hex(16)
            self.manual_hold = (token, session, direction, monotonic() + .5, owner, engine.control_generation)
            return {'token': token}
        hold = self.manual_hold
        if not hold or hold[0] != body.get('token') or hold[1] is not session:
            raise ValueError('현재 수동 입력을 다시 시작하세요.')
        _, _, direction, start_before, owner, generation = hold
        if action == 'manual_release':
            self.manual_hold = None
            session.release_web_hold()
        elif owner != engine.client_owner or generation != engine.control_generation:
            raise ValueError('제어권 또는 안전 상태가 바뀌었습니다. 방향 버튼을 다시 누르세요.')
        elif start_before is None:
            session.renew_web_hold()
        else:
            self.manual_hold = None
            if monotonic() >= start_before:
                raise ValueError('수동 입력 시작 시간이 지났습니다.')
            engine.begin_hold(direction, remote=True)
            self.manual_hold = (hold[0], session, direction, None, owner, generation)
        return {'message': '수동 입력을 갱신했습니다.'}

    def _follow(self, action, body):
        engine = self.engine
        state, result = engine.render_state()
        frame = result['frame'] if result else None
        now = monotonic()
        age_limit = engine.cfg['safety']['perception_timeout_s']
        if not (self.stream and frame and state and state['perception_fresh'] and 0 <= now-frame.timestamp <= age_limit):
            raise ValueError('최신 인식 결과를 기다려 주세요.')
        target = body.get('id') if action == 'select_target' else engine.c.target_id
        selected_at = body.get('frame') if action == 'select_target' else frame.timestamp
        if (type(target) is not int or not any(p.track_id == target for p in frame.people)
                or type(selected_at) not in (float, int)
                or not max(now-age_limit, self.camera_started_at) <= selected_at <= frame.timestamp):
            raise ValueError('최신 영상에서 추종 대상을 다시 선택하세요.')
        if action == 'resume_follow':
            if self.shared and engine.client:
                self._check_vision_operator(body, 'GESTURE_FOLLOW')
            engine.resume_follow()
            if self.shared and engine.client:
                self._lease_vision(body['viewer'])
        else:
            engine.request('SELECT', target)


def handler(web, port):
    authority = f'127.0.0.1:{port}'
    pages = {'/': ('index.html', 'text/html; charset=utf-8'),
             '/style.css': ('style.css', 'text/css; charset=utf-8'),
             '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
             '/dig-avatar-smile.png': ('dig-avatar-smile.png', 'image/png'),
             '/dig-avatar-thinking.png': ('dig-avatar-thinking.png', 'image/png')}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, body, kind, status=200):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def send_json(self, value, status=200):
            self.send(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), 'application/json; charset=utf-8', status)

        def stream_agent_reply(self, stream_id):
            self.close_connection = True
            self.connection.settimeout(5)
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                previous = None
                deadline = monotonic() + 75
                while monotonic() < deadline:
                    active, reply = web.agent.wait_reply(stream_id, previous, timeout=5)
                    if not active:
                        self.wfile.write(b'data: {"done":true}\n\n')
                        return
                    if reply == previous:
                        self.wfile.write(b': keepalive\n\n')
                    else:
                        self.wfile.write(('data: ' + json.dumps({'text': reply}, ensure_ascii=False) + '\n\n').encode())
                        previous = reply
            except OSError:
                return

        def do_GET(self):
            if self.headers.get('Host') != authority:
                self.send_json({'error': '로컬 관제 주소를 사용하세요.'}, 403)
            elif self.path == '/api/state':
                self.send_json(web.state())
            elif web.agent and (match := re.fullmatch(r'/api/agent/stream/([0-9a-f]{32})', self.path)):
                if (self.headers.get('Origin') not in (None, f'http://{authority}') or
                        self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                    self.send_json({'error': '관제 화면에서 요청해 주세요.'}, 403)
                else:
                    self.stream_agent_reply(match[1])
            elif self.path == '/api/draft' and web.agent:
                try:
                    self.send_json({'draft': web.agent.drafts.current()[1]})
                except sqlite3.Error:
                    self.send_json({'error': '초안 저장소를 읽을 수 없습니다.'}, 503)
            elif self.path.startswith('/api/knowledge/') and web.agent:
                library = web.agent.knowledge
                path, _, query = self.path.partition('?')
                try:
                    if path == '/api/knowledge/documents':
                        self.send_json({'documents': library.documents()})
                    elif path == '/api/knowledge/search':
                        text = parse_qs(query).get('q', [''])[0].strip()
                        if not 0 < len(text) <= 350:
                            raise KnowledgeError('검색어를 1~350자로 입력하세요.')
                        self.send_json({'results': library.search_robot_documents(text)})
                    elif (match := re.fullmatch(r'/api/knowledge/chunks/([1-9][0-9]{0,9})', path)) and (chunk := library.get_document_chunk(int(match[1]))):
                        self.send_json(chunk)
                    else:
                        self.send_json({'error': '문서 발췌를 찾을 수 없습니다.'}, 404)
                except KnowledgeError as exc:
                    self.send_json({'error': str(exc)}, 400)
                except (OSError, sqlite3.Error):
                    self.send_json({'error': '문서 색인을 읽을 수 없습니다.'}, 503)
            elif self.path in ('/api/history', '/api/history/latest') or re.fullmatch(r'/api/history/[0-9a-f]{32}', self.path):
                history = web.engine.language.history
                try:
                    if self.path == '/api/history':
                        self.send_json({'runs': history.recent(), 'degraded': history.degraded})
                    elif self.path == '/api/history/latest':
                        latest = history.recent(limit=1)
                        self.send_json({'run': history.detail(latest[0]['run_id']) if latest else None})
                    elif (run := history.detail(self.path.rsplit('/', 1)[1])) is not None:
                        self.send_json(run)
                    else:
                        self.send_json({'error': '실행 기록을 찾을 수 없습니다.'}, 404)
                except HistoryUnavailable:
                    self.send_json({'error': '실행 이력 저장소를 읽을 수 없습니다.'}, 503)
            elif self.path.partition('?')[0] == '/api/camera':
                try:
                    query = parse_qs(self.path.partition('?')[2])
                    self.send_json(web.camera(query.get('viewer', [None])[0]))
                except ValueError as exc:
                    self.send_json({'error': str(exc)}, 503)
            elif self.path in pages:
                file, kind = pages[self.path]
                self.send((STATIC / file).read_bytes(), kind)
            else:
                name = self.path.removeprefix('/icons/')
                if self.path.startswith('/icons/') and name.endswith('.svg') and name[:-4].replace('-', '').isalnum() and (ICONS / name).is_file():
                    self.send((ICONS / name).read_bytes(), 'image/svg+xml')
                else:
                    self.send_json({'error': '찾을 수 없습니다.'}, 404)

        def do_POST(self):
            upload = self.path == '/api/knowledge/documents'
            if self.path not in ('/api/control', '/api/draft', '/api/knowledge/documents') or (self.path != '/api/control' and not web.agent):
                self.send_json({'error': '찾을 수 없습니다.'}, 404)
                return
            if (self.headers.get('Host') != authority or self.headers.get('Origin') != f'http://{authority}'
                    or self.headers.get_content_type() != 'application/json'):
                self.send_json({'error': '관제 화면에서 요청해 주세요.'}, 403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= (4 * ((MAX_DOCUMENT+2)//3) + 4096 if upload else 4096):
                    raise ValueError('요청 크기를 확인하세요.')
                self.connection.settimeout(3)
                payload = self.rfile.read(length)
                body = strict_json_object(payload, parse_float=Decimal) if self.path == '/api/draft' else json.loads(payload)
            except (ValueError, OSError):
                self.send_json({'error': '요청 형식을 확인하세요.'}, 400)
                return
            try:
                if upload:
                    if not isinstance(body, dict) or set(body) != {'name', 'content'} or not isinstance(body['content'], str):
                        raise KnowledgeError('파일 첨부 형식을 확인하세요.')
                    try:
                        raw = base64.b64decode(body['content'], validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise KnowledgeError('파일 첨부 형식을 확인하세요.') from exc
                    document = web.agent.knowledge.register(body['name'], raw)
                    self.send_json({'document': document})
                elif self.path == '/api/draft':
                    message, draft = web.agent.apply_draft(DraftOperation.model_validate(body))
                    self.send_json({'message': message, 'draft': draft})
                else:
                    self.send_json(web.command(body))
            except DraftConflict:
                self.send_json({'error': '다른 화면에서 초안이 변경됐습니다. 최신 버전을 확인하세요.', 'code': 'STALE_REVISION'}, 409)
            except (OSError, sqlite3.Error):
                self.send_json({'error': '로컬 저장소를 지금 사용할 수 없습니다.'}, 503)
            except ValueError as exc:
                self.send_json({'error': str(exc)}, 400)

    return Handler


def run_web_dashboard(config, *, camera=False, port=8765, agent_enabled=False):
    if not 1 <= port <= 65535:
        raise ValueError('웹 관제 포트는 1~65535여야 합니다.')
    if not config['communication']['host']:
        config['communication']['host'] = load_connection(ROOT.parent / 'settings' / 'connection.json')
    engine = DashboardEngine(config, ROOT.parent / 'logs', 'LIVE_CAMERA' if camera else 'SYNTHETIC_PREVIEW', logger=False)
    engine.central_ui = True
    web = WebDashboard(engine, (lambda: VisionStream(config)) if camera else PreviewStream)
    try:
        if agent_enabled:
            from python_controller.llm.unified_agent import UnifiedAgent
            web.agent = UnifiedAgent(engine)
        with ThreadingHTTPServer(('127.0.0.1', port), handler(web, port)) as server:
            engine.start_control_loop(web.update_camera, on_exit=web.stop_camera_views)
            print(f'D.I.G. Web · http://127.0.0.1:{port}', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.request('ESTOP')
        engine.close()
        web.close_camera()
        if web.agent:
            web.agent.close()
    return 0


def bind_shared_web(port):
    if not 1 <= port <= 65535:
        raise ValueError('웹 관제 포트는 1~65535여야 합니다.')
    return ThreadingHTTPServer(('127.0.0.1', port), None)


def start_shared_web(engine, stream, agent, server):
    port = server.server_port
    if not engine.cfg['communication']['host']:
        engine.cfg['communication']['host'] = load_connection(ROOT.parent / 'settings' / 'connection.json')
    web = WebDashboard(engine, None, agent, shared_stream=stream)
    server.RequestHandlerClass = handler(web, port)
    Thread(target=server.serve_forever, name='qt-shared-web', daemon=True).start()
    url = f'http://127.0.0.1:{port}/'
    print(f'D.I.G. Qt 공유 관제 · {url}', flush=True)
    return url
