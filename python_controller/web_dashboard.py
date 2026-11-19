"""Local web console backed by the D.I.G. simulation supervisor."""
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic

from python_controller.communication.motor_test import load_connection
from python_controller.dashboard_runtime import DashboardEngine, PreviewStream, VisionStream
from python_controller.planning.dashboard_command import normalize_dashboard_command
from python_controller.planning.schema import describe_action
from python_controller.planning.validator import ValidatedPlan, parse_operator_command
from python_controller.visualization.korean import ko

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'web'
ICONS = ROOT / 'visualization' / 'assets' / 'icons'


class WebDashboard:
    def __init__(self, engine, stream, agent=None):
        self.engine = engine
        self.stream = stream
        self.plan = None
        self.agent = agent

    def state(self):
        with self.engine.control_lock:
            data, _ = self.engine.render_state()
            data = data or {}
            tr = data.get('transport', {})
            radar = tr.get('radar') or data.get('radar') or {'state': 'UNKNOWN', 'blocked': True, 'reason': 'RADAR_NO_DATA'}
            estop = self.engine.c.estop_latched
            code = 'EMERGENCY_STOP' if estop else data.get('stop_reason', 'STARTING')
            cfg = self.engine.cfg
            return {
                'source': self.engine.source, 'owner': self.engine.active_control_mode,
                'state': ko(data.get('state', 'STARTING')), 'stop_code': code, 'stop_reason': ko(code),
                'connected': bool(tr.get('connected')), 'ready': bool(tr.get('ready')),
                'stop_verified': bool(tr.get('stop_verified')), 'estop_latched': estop,
                'radar': {**radar, 'detail': ko(radar['reason'])}, 'following': bool(data.get('following')) and not estop,
                'armed': self.engine.c.armed, 'target': self.engine.c.target_id,
                'people': data.get('detected_people'), 'fps': data.get('fps'), 'processing_ms': data.get('processing_ms'),
                'camera_status': (data.get('pipeline_health') or {}).get('camera'), 'vision_status': ko(self.stream.status),
                'perception_fresh': bool(data.get('perception_fresh')), 'vision': data.get('vision', {}),
                'gesture': data.get('gesture', {}), 'safe': data.get('safe'), 'motors': data.get('motors', {}),
                'validation': tr.get('validation_detail'), 'recovery': tr.get('recovery_state'),
                'fault': tr.get('fault'), 'notice': self.engine.control_error or data.get('notice'),
                'tests': self.engine.book.rows, 'plan': self.plan,
                'agent': self.agent.view() if self.agent else None,
                'settings': {'esp32': cfg['communication']['host'], 'radar': cfg['radar']['source_ip'],
                             'hand': self.engine.hand.mapping, 'follow': cfg['gesture']['mapping'],
                             'timeout_ms': round(1000*cfg['safety']['perception_timeout_s']),
                             'hold_s': cfg['gesture']['hold_s'], 'camera_index': cfg['camera']['index'],
                             'image_size': cfg['vision']['image_size']},
            }

    def camera(self):
        state, result = self.engine.render_state()
        capture = self.stream.latest_capture() if hasattr(self.stream, 'latest_capture') else None
        now = monotonic()
        timeout = self.engine.cfg['safety']['perception_timeout_s']
        if getattr(self.stream, 'camera_status', None) == 'CAMERA_ERROR':
            return {'image': None, 'status': '카메라 오류', 'people': []}
        frame = result['frame'] if result else None
        age = now-frame.timestamp if frame else float('inf')
        if frame and frame.camera_ok and 0 <= age <= max(1, timeout) and not self.stream.error:
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
            if engine.client or engine.language.executing or engine.control_error:
                raise ValueError('시뮬레이션 상태에서만 요청할 수 있습니다.')
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
                             'distance_cm': format(sum(a.distance_cm for a in plan.actions if a.type == 'move'), 'f'),
                             'rotation': plan.requires_angle_control}
                return {'message': '이동 방향과 거리를 확인하세요.'}
            if action == 'discard_plan':
                self.plan = None
                return {'message': '계획을 지웠습니다.'}
            if action == 'pause':
                engine.request('PAUSE')
            elif action == 'clear_target':
                engine.request('CLEAR')
            elif action in ('select_target', 'resume_follow'):
                self._follow(action, body)
            else:
                raise ValueError('이 명령은 웹 관제 1차에서 지원하지 않습니다.')
        return {'message': '시뮬레이션 제어 요청을 전달했습니다.'}

    def _follow(self, action, body):
        engine = self.engine
        state, result = engine.render_state()
        frame = result['frame'] if result else None
        now = monotonic()
        age_limit = engine.cfg['safety']['perception_timeout_s']
        if not (frame and state and state['perception_fresh'] and 0 <= now-frame.timestamp <= age_limit):
            raise ValueError('최신 인식 결과를 기다려 주세요.')
        target = body.get('id') if action == 'select_target' else engine.c.target_id
        selected_at = body.get('frame') if action == 'select_target' else frame.timestamp
        if (type(target) is not int or not any(p.track_id == target for p in frame.people)
                or type(selected_at) not in (float, int) or not now-age_limit <= selected_at <= frame.timestamp):
            raise ValueError('최신 영상에서 추종 대상을 다시 선택하세요.')
        if action == 'resume_follow':
            engine.resume_follow()
        else:
            engine.request('SELECT', target)


def handler(web, port):
    authority = f'127.0.0.1:{port}'
    pages = {'/': ('index.html', 'text/html'), '/style.css': ('style.css', 'text/css'),
             '/app.js': ('app.js', 'text/javascript')}

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

        def do_GET(self):
            if self.headers.get('Host') != authority:
                self.send_json({'error': '로컬 관제 주소를 사용하세요.'}, 403)
            elif self.path == '/api/state':
                self.send_json(web.state())
            elif self.path == '/api/camera':
                try:
                    self.send_json(web.camera())
                except ValueError as exc:
                    self.send_json({'error': str(exc)}, 503)
            elif self.path in pages:
                file, kind = pages[self.path]
                self.send((STATIC / file).read_bytes(), kind + '; charset=utf-8')
            else:
                name = self.path.removeprefix('/icons/')
                if self.path.startswith('/icons/') and name.endswith('.svg') and name[:-4].replace('-', '').isalnum() and (ICONS / name).is_file():
                    self.send((ICONS / name).read_bytes(), 'image/svg+xml')
                else:
                    self.send_json({'error': '찾을 수 없습니다.'}, 404)

        def do_POST(self):
            if self.path != '/api/control':
                self.send_json({'error': '찾을 수 없습니다.'}, 404)
                return
            if (self.headers.get('Host') != authority or self.headers.get('Origin') != f'http://{authority}'
                    or self.headers.get_content_type() != 'application/json'):
                self.send_json({'error': '관제 화면에서 요청해 주세요.'}, 403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('요청 크기를 확인하세요.')
                self.connection.settimeout(3)
                body = json.loads(self.rfile.read(length))
            except (ValueError, OSError):
                self.send_json({'error': '요청 형식을 확인하세요.'}, 400)
                return
            try:
                self.send_json(web.command(body))
            except ValueError as exc:
                self.send_json({'error': str(exc)}, 400)

    return Handler


def run_web_dashboard(config, *, camera=False, port=8765, agent_enabled=False):
    if not 1 <= port <= 65535:
        raise ValueError('웹 관제 포트는 1~65535여야 합니다.')
    if not config['communication']['host']:
        config['communication']['host'] = load_connection(ROOT.parent / 'settings' / 'connection.json')
    stream = VisionStream(config) if camera else PreviewStream()
    engine = DashboardEngine(config, ROOT.parent / 'logs', 'LIVE_CAMERA' if camera else 'SYNTHETIC_PREVIEW', logger=False)
    engine.central_ui = True
    try:
        agent = None
        if agent_enabled:
            from python_controller.llm.unified_agent import UnifiedAgent
            agent = UnifiedAgent(engine)
        with ThreadingHTTPServer(('127.0.0.1', port), handler(WebDashboard(engine, stream, agent), port)) as server:
            stream.start()
            engine.start_control_loop(stream)
            print(f'D.I.G. Web · http://127.0.0.1:{port}', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.request('ESTOP')
        engine.close()
        stream.close()
    return 0
