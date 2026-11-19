"""Latest-only capture/inference workers and UI-independent dashboard controller."""
from dataclasses import asdict
from concurrent.futures import Future, wait
from functools import wraps
import copy
import math
from pathlib import Path
import threading
import time

from python_controller.control.state_machine import Controller
from python_controller.control.hand_drive import HandDrive, HandDriveClient
from python_controller.models import MotionCommand,PerceptionFrame,PersonObservation,GestureObservation
from python_controller.monitoring import SessionLogger,TestBook,motor_feedback
from python_controller.vision.person_tracker import TargetSelector
from python_controller.vision.gesture_recognizer import gesture_diagnostics


def control_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.control_lock:
            return method(self, *args, **kwargs)
    return locked


class LatestSlot:
    def __init__(self):
        self.lock=threading.Lock()
        self.value=None
        self.version=0

    def put(self,value):
        with self.lock:
            self.value=value;self.version+=1

    def get(self):
        with self.lock: return self.version,self.value


class VisionStream:
    def __init__(self,config):
        self.cfg=config
        self.capture,self.results=LatestSlot(),LatestSlot()
        self.done=threading.Event()
        self.error=None
        self.tracking_error=None
        self.status='LOADING_MODELS'
        self.camera_status='OPENING_CAMERA'
        self.threads=[]
        self.hand_only=False

    def start(self):
        for method,name in [(self._camera,'camera-latest'),(self._vision,'vision-latest')]:
            thread=threading.Thread(target=method,name=name,daemon=True)
            self.threads.append(thread);thread.start()

    def _camera(self):
        camera=None
        try:
            from python_controller.vision.camera import Camera
            if self.done.is_set(): return
            camera=Camera(self.cfg['camera'])
            self.camera_status='WAITING_FOR_FRAMES'
            while not self.done.is_set():
                started=time.monotonic()
                image,timestamp=camera.read()
                self.capture.put((image,timestamp,(time.monotonic()-started)*1000))
                self.camera_status='CAPTURING'
        except Exception as exc:
            self.camera_status='CAMERA_ERROR'
            self.error=f'CAMERA: {type(exc).__name__}: {exc}'
        finally:
            if camera: camera.close()

    def _vision(self):
        recognizer=None
        try:
            from python_controller.vision.person_detector import PersonDetector
            from python_controller.vision.gesture_recognizer import GestureRecognizer
            if self.done.is_set(): return
            self.status='LOADING_MEDIAPIPE'
            recognizer=GestureRecognizer(self.cfg['vision'],self.cfg['gesture'])
            detector=None
            version=0;last_completed=None;dropped=0;last_capture=None
            self.status='WAITING_FOR_CAMERA'
            while not self.done.is_set():
                current,value=self.capture.get()
                if value is None or current==version:
                    self.done.wait(.005);continue
                dropped += current-version-1
                version=current
                hand_only=self.hand_only
                if not hand_only and detector is None and not self.tracking_error:
                    try:
                        self.status='LOADING_YOLO'
                        detector=PersonDetector(self.cfg['vision'])
                    except Exception as exc:
                        self.tracking_error=f'YOLO: {type(exc).__name__}: {exc}'
                    # Model initialization can take seconds. Resample the capture slot.
                    current,value=self.capture.get()
                    dropped+=current-version
                    version=current
                    hand_only=self.hand_only
                if self.done.is_set(): break
                image,timestamp,capture_ms=value
                started=time.monotonic()
                if started-timestamp > self.cfg['safety']['perception_timeout_s']:
                    self.status='CAPTURE_STALE_BEFORE_INFERENCE'
                    continue
                people=()
                if not hand_only and detector is not None and not self.tracking_error:
                    try:
                        self.status='INFERENCING_YOLO'
                        people=detector.detect(image,timestamp)
                    except Exception as exc:
                        self.tracking_error=f'YOLO: {type(exc).__name__}: {exc}'
                hand_started=time.monotonic()
                target_id=getattr(self,'target_id',None)
                self.status='INFERENCING_GESTURE'
                gesture,hands=recognizer.recognize(image,people,target_id,timestamp)
                completed=time.monotonic()
                self.results.put(dict(frame=PerceptionFrame(timestamp,image.shape[1],image.shape[0],people,gesture),
                                      image=image,hands=hands,vision=recognizer.diagnostics,
                                      completed_at=completed,latency_ms=(completed-timestamp)*1000,
                                      hand_only=hand_only,metrics=dict(capture_ms=capture_ms,queue_ms=max(0,(started-timestamp)*1000-capture_ms),
                                          capture_fps=(current-last_capture[0])/(timestamp-last_capture[1])
                                          if last_capture and timestamp>last_capture[1] else None,
                                          yolo_ms=None if hand_only or self.tracking_error else (hand_started-started)*1000,
                                          mediapipe_ms=(completed-hand_started)*1000,skipped_capture_frames=dropped),
                                      fps=1/(completed-last_completed) if last_completed else None))
                last_completed=completed
                last_capture=(current,timestamp)
                self.status=('RUNNING' if completed-timestamp <= self.cfg['safety']['perception_timeout_s']
                             else 'INFERENCE_RESULT_STALE')
        except Exception as exc:
            self.error=f'VISION: {type(exc).__name__}: {exc}'
        finally:
            if recognizer: recognizer.close()

    def latest(self):
        return self.results.get()[1]

    def latest_capture(self):
        return self.capture.get()[1]

    @property
    def stopped(self):
        return not any(thread.is_alive() for thread in self.threads)

    def stop(self):
        self.done.set()

    def close(self):
        self.stop()
        for thread in self.threads: thread.join(timeout=.5)


class PreviewStream:
    """Explicit synthetic UI preview; never eligible for hardware connection."""
    error=None
    status='SYNTHETIC_PREVIEW'
    target_id=None
    stopped=True
    def start(self): pass
    def latest(self):
        import numpy as np
        now=time.monotonic()
        people=(PersonObservation(7,(220,65,420,410),.94,now),)
        g=GestureObservation('Open_Palm',.92,7,now) if self.target_id==7 else None
        return dict(frame=PerceptionFrame(now,640,480,people,g),image=np.full((480,640,3),(31,39,48),dtype=np.uint8),
                    hands=[],vision=gesture_diagnostics([('Open_Palm',.92,7)],self.target_id,1,now,1),
                    completed_at=now,latency_ms=None,fps=None)
    def stop(self): pass
    def close(self): self.stop()


class DashboardEngine:
    def __init__(self,config,folder,source='LIVE_CAMERA',client_factory=None,logger=True,radar=None):
        self.control_lock = threading.RLock()
        self.control_done = threading.Event()
        self.control_thread = None
        self.control_error = None
        self.seen_recovery_generation = 0
        self.cfg=config
        self.source=source
        self.c=Controller(config)
        self.client=None
        self.client_factory=client_factory
        self.radar = radar
        self.radar_receiver = None
        self.seen_radar_generation = None
        self.radar_neutral_required = False
        self.pending=None
        self.last_result=None
        self.snapshot=None
        self.notice='ESP32 주소를 입력하거나 손동작 탭에서 카메라를 확인하세요.'
        self.book=TestBook()
        self.logger=SessionLogger(folder,'SIMULATION',source) if logger else None
        if self.logger:self.logger.save_tests(self.book)
        self.error=None
        self.last_motor_snapshot=None
        self.connection_attempt=None
        self.last_transport=None
        self.last_release=None
        self.client_owner=None
        self.hand = HandDrive()
        self.hand_armed_once = False
        self.hand_requires_activation = False
        self.hand_connected_at = None
        self.follow_auto_arm = False
        self.language_authorized_host = None
        self.release_future = None
        self.pending_mode = None
        self.pending_mode_auto_arm = True
        self.control_generation = 0
        self.central_ui = False
        from python_controller.llm.central_control import NaturalControl
        self.language = NaturalControl(folder, config['motor_test']['ui_lease_s'])

    @property
    def mode(self): return 'HARDWARE' if self.client or self.language.executing else 'SIMULATION'

    @property
    def active_control_mode(self):
        if self.language.executing:
            return "LLM_COMMAND"
        return self.client_owner or "SIMULATION"

    @property
    def shared_session(self):
        return self.client if getattr(self.client, 'central_session', False) is True else None

    @property
    def control_running(self):
        return bool(self.control_thread and self.control_thread.is_alive())

    def start_control_loop(self, stream, *, on_exit=None):
        if self.control_thread is not None:
            raise ValueError('중앙 제어 감독 작업은 이미 시작됐습니다.')

        def supervise():
            try:
                while not self.control_done.is_set():
                    self.finish_disconnect()
                    with self.control_lock:
                        active = stream() if callable(stream) else stream
                        if active is None:
                            self.tick(None, pipeline_error=self.error,
                                      pipeline_health={'camera': 'CAMERA_OFF', 'vision': 'CAMERA_OFF'})
                        else:
                            active.hand_only = self.client_owner == 'GESTURE_DIRECT'
                            capture = active.latest_capture() if hasattr(active,'latest_capture') else None
                            health = dict(vision=active.status,camera=getattr(active,'camera_status',None),
                                          tracking_error=getattr(active,'tracking_error',None),
                                          capture_age_ms=max(0,(time.monotonic()-capture[1])*1000) if capture else None)
                            error = active.error or self.error
                            if self.active_control_mode in ('SIMULATION','GESTURE_FOLLOW'):
                                error = error or health['tracking_error']
                            self.tick(active.latest(),pipeline_error=error,pipeline_health=health)
                            active.target_id = self.c.target_id
                    self.control_done.wait(.05)
            except Exception as exc:
                self.control_error = f'CONTROL_SUPERVISOR_FAILED: {type(exc).__name__}'
                self.request('ESTOP')
            finally:
                if on_exit is not None:
                    on_exit()

        self.control_thread = threading.Thread(target=supervise,name='central-control-supervisor',daemon=True)
        self.control_thread.start()

    def operator_alive(self):
        client = self.shared_session
        if client:
            client.operator_alive()

    @control_locked
    def render_state(self):
        return self.snapshot,self.last_result

    @control_locked
    def save_radar_source(self, path, host):
        from python_controller.config import radar_source_ip, save_radar_settings

        if self.client or self.release_future or self.language.executing:
            raise ValueError('ESP32를 정지 후 연결 해제한 다음 Pi 주소를 저장하세요.')
        host = radar_source_ip(host)
        save_radar_settings(path, host, self.cfg['radar']['timeout_s'])
        if self.radar_receiver:
            self.radar_receiver.close()
            self.radar_receiver = None
        self.radar = None
        self.seen_radar_generation = None
        self.cfg['radar']['source_ip'] = host
        self.notice = '레이더 Pi 주소를 저장했습니다.' if host else '레이더 Pi 주소를 비웠습니다. 이동은 차단됩니다.'

    def _sync_radar(self, now):
        if self.radar is None:
            return None
        radar = self.radar.snapshot(now)
        if radar['blocked'] and radar['generation'] != self.seen_radar_generation:
            self.seen_radar_generation = radar['generation']
            self.language.cancel(radar['reason'])
            self.pending = None
            self.follow_auto_arm = False
            self.hand_armed_once = True
            self.radar_neutral_required = True
            self.hand.reset()
            self.c.halt('PAUSED', radar['reason'], True)
            if self.logger:
                self.logger.write(dict(event='RADAR_BLOCKED', radar=radar), True)
        return radar

    @control_locked
    def rearm_radar(self):
        session = self.shared_session
        if session is None or self.release_future or self.language.executing:
            raise ValueError('ESP32 연결과 이전 실행 종료를 확인하세요.')
        self._sync_radar(session.clock())
        session.rearm_radar()
        self.pending = None
        self.hand.reset()
        self.hand_connected_at = session.clock()
        self.hand_armed_once = self.c.estop_latched or self.hand_requires_activation
        self.radar_neutral_required = True
        self.follow_auto_arm = False
        self.notice = '레이더 CLEAR 확인 · 새 이동 입력 대기'
        if self.logger:
            self.logger.write(dict(event='RADAR_REARMED', radar=session.radar_status()), True)

    @control_locked
    def recover_robot(self):
        if not self.shared_session or self.release_future:
            raise ValueError('연결된 중앙 제어 세션을 확인하세요.')
        self.language.cancel()
        self.pending_mode = None
        self.shared_session.recover()
        self.notice = '정지 전용 세션 동기화 중'

    @control_locked
    def set_encoder_policy(self, policy):
        if not self.shared_session or self.language.executing or self.release_future:
            raise ValueError('출력 정지와 중앙 연결을 확인하세요.')
        return self.shared_session.set_encoder_policy(policy)

    @control_locked
    def configure_hand(self, *, activate=True, **settings):
        hand = HandDrive(**settings)
        client = self.shared_session
        if client and self.client_owner == 'GESTURE_DIRECT':
            client.stop('HAND_SETTINGS_CHANGED')
            self.hand_connected_at = client.clock()
            if activate:
                self.c.estop_latched = False
        self.hand = hand
        self.hand_requires_activation = not activate
        self.hand_armed_once = not activate or self.c.estop_latched

    @control_locked
    def connect_robot(self, host):
        if self.source != 'LIVE_CAMERA':
            raise ValueError('합성 미리보기에서는 실제 장치를 연결할 수 없습니다.')
        if self.client or self.language.executing:
            raise ValueError('기존 연결을 먼저 해제하세요.')
        from python_controller.communication.motor_test import connection_host
        from python_controller.communication.robot_session import RobotSession
        host = connection_host(host)
        config = copy.deepcopy(self.cfg)
        config['communication']['host'] = host
        client = (self.client_factory or RobotSession)(config)
        if self.radar is None and self.client_factory is None:
            from python_controller.control.safety_supervisor import SafetySupervisor
            from python_controller.radar.udp_receiver import RadarReceiver
            self.radar = SafetySupervisor(config['radar']['source_ip'], config['radar']['timeout_s'])
            self.radar_receiver = RadarReceiver(self.radar)
            self.radar_receiver.start()
        client.safety = self.radar
        client.configure_owner('DIRECT_CONTROL', self._previous_session(host, stationary_only=True))
        self.connection_attempt = dict(status='PENDING', host=host, phase='CONFIG', checked_at=time.time())
        try:
            client.start()
        except Exception:
            client.close()
            self.connection_attempt.update(status='FAILED',phase=client.startup_phase)
            raise
        self.client, self.client_owner = client, 'DIRECT_CONTROL'
        self.seen_recovery_generation = client.recovery_generation
        self.connection_attempt.update(status='STARTED', phase='RX_BOUND')
        self.c.hardware = True
        self.c.estop_latched = False
        self.pending = None
        self.notice = '중앙 연결 중 · ESP32 정지 응답 확인'
        if self.logger:
            self.logger.mode = 'HARDWARE'
            self.logger.write(dict(event='ROBOT_CONNECTED', session_id=client.protocol.session_id,
                                   host=host, controller_owner=self.client_owner), True)

    @control_locked
    def select_control_mode(self, mode, *, auto_arm=True):
        from python_controller.communication.robot_session import CONTROL_MODES
        if mode not in CONTROL_MODES:
            raise ValueError('지원하지 않는 제어 기능입니다.')
        client = self.shared_session
        if client is None:
            return
        if self.release_future:
            raise ValueError('연결 해제 중입니다.')
        if mode == self.client_owner and self.pending_mode is None:
            return
        if self.language.executing:
            self.language.cancel('MODE_CHANGE')
        client.stop('MODE_CHANGE')
        self.control_generation += 1
        self.pending_mode = mode
        self.pending_mode_auto_arm = auto_arm
        self.pending = None
        self.c.halt('PAUSED', 'MODE_CHANGE', True)
        self.notice = '기능 전환 중 · 정지 응답 대기'

    def _finish_mode_change(self):
        client = self.shared_session
        if not client or self.pending_mode is None or self.release_future or self.language.executing:
            return
        status = client.snapshot()
        if not status['stop_verified'] or not status['ready']:
            return
        mode = self.pending_mode
        allow_auto_arm = self.pending_mode_auto_arm and not self.c.estop_latched
        client.select_mode(mode)
        self.pending_mode = None
        self.client_owner = mode
        self.c.halt('PAUSED', 'MODE_READY', True)
        self.hand.reset()
        self.hand_requires_activation = mode == 'GESTURE_DIRECT' and not self.pending_mode_auto_arm
        self.hand_armed_once = not allow_auto_arm
        self.hand_connected_at = client.clock()
        self.follow_auto_arm = allow_auto_arm and mode == 'GESTURE_FOLLOW'
        self.notice = '제어 기능 준비됨'
        if self.logger:
            self.logger.write(dict(event='CONTROL_MODE_CHANGED', controller_owner=mode,
                                   session_id=client.protocol.session_id), True)

    def _take_language_release(self):
        if self.language.release is not None:
            self.last_release, self.language.release = self.language.release, None
            if not self.last_release["stop_verified"] or self.last_release.get("fault") not in (None, "NONE"):
                self.language_authorized_host = None

    def _previous_session(self, host, *, stationary_only=False):
        self._take_language_release()
        if self.last_release:
            if not self.last_release["socket_closed"]:
                raise ValueError("이전 송신기의 종료를 확인하세요.")
            if not self.last_release["stop_verified"]:
                if not stationary_only or self.last_release["host"] != host:
                    raise ValueError("이전 정지가 미확인입니다. 같은 ESP32를 모터 시험의 통신 전용으로 다시 연결하세요.")
                return None
            if self.last_release["host"] == host:
                return self.last_release
        return None

    def connect_hardware(self,host,acknowledged):
        # Compatibility entry point; automatic hardware following is no longer exposed.
        if self.cfg['communication']['signs_verified'] is not True:
            raise ValueError('Physical signs are unverified; use the MOTOR TEST connection instead')
        self.connect_motor_test(host,acknowledged)

    def connect_motor_test(self,host,acknowledged):
        if self.source!='LIVE_CAMERA': raise ValueError('Synthetic observations cannot enable HARDWARE')
        if acknowledged is not True: raise ValueError('Explicit hardware authorization required')
        if self.language.executing: raise ValueError('자연어 장치 제어가 끝날 때까지 기다리세요.')
        if self.client: raise ValueError('Already connected; stop and disconnect first')
        from python_controller.communication.motor_test import MotorTestClient
        config=copy.deepcopy(self.cfg);config['communication']['host']=host
        self.connection_attempt=dict(status='PENDING',host=host,phase='CONFIG',checked_at=time.time())
        try:
            client=(self.client_factory or MotorTestClient)(config)
            if hasattr(client, "configure_owner"):
                client.configure_owner("MANUAL_MOTOR_TEST", self._previous_session(host, stationary_only=True))
            client.start()  # STOP proof followed by stationary heartbeat. No motor authorization.
        except Exception as exc:
            self.connection_attempt.update(status='FAILED',phase=getattr(locals().get('client'),'startup_phase','CONFIG'),
                                           error_code=getattr(exc,'winerror',None) or getattr(exc,'errno',None),error_type=type(exc).__name__)
            if self.logger:
                failed_client=locals().get('client')
                if hasattr(failed_client,'drain_transport_events'):
                    for event in failed_client.drain_transport_events():self.logger.write(event,True)
                self.logger.write(dict(event='CONNECTION_FAILED',attempt=dict(self.connection_attempt)),True)
            raise
        self.connection_attempt.update(status='STARTED',phase='RX_BOUND')
        self.language_authorized_host = None
        self.client=client
        self.client_owner="MANUAL_MOTOR_TEST"
        self.last_motor_snapshot=None
        self.notice='직접 제어 연결 중. 유효한 STOP 응답을 확인합니다.'
        self.c.hardware=True
        self.c.halt('PAUSED','MODE_CHANGED_REARM_REQUIRED',True)
        if self.logger:
            self.logger.mode='HARDWARE'
            self.logger.write(dict(event='MODE_CHANGED',state='PAUSED',control_source='MANUAL_MOTOR_TEST'),True)

    def connect_gesture_follow(self, host, acknowledged, directions_verified, *, auto_arm=False):
        if self.source != "LIVE_CAMERA":
            raise ValueError("합성 미리보기에서 실제 장치 실행은 차단됩니다.")
        if self.client or self.language.executing:
            raise ValueError("다른 제어를 정지하고 연결을 해제하세요.")
        if acknowledged is not True or directions_verified is not True:
            raise ValueError("손동작 추종 1회 승인과 실물 방향·시험 준비 확인이 필요합니다.")
        from python_controller.communication.esp32_udp import UdpClient
        config = copy.deepcopy(self.cfg)
        config["communication"].update(host=host, signs_verified=True)
        client = (self.client_factory or UdpClient)(config)
        client.configure_owner("GESTURE_FOLLOW", self._previous_session(host))
        client.start()
        self.language_authorized_host = None
        self.client, self.client_owner = client, "GESTURE_FOLLOW"
        self.follow_auto_arm = auto_arm
        self.c.hardware = True
        self.c.halt("PAUSED", "MODE_CHANGED_REARM_REQUIRED", True)
        self.pending = None
        self.notice = "사람 추종 연결. 목표 확정 후 손바닥을 펼치세요."
        if self.logger:
            self.logger.mode = "HARDWARE"
            self.logger.write(dict(event="MODE_CHANGED", active_control_mode=self.client_owner,
                                   controller_owner=self.client_owner), True)

    @control_locked
    def tune_tracking(self, perception, hold, frames, max_height, image_size):
        if (self.client and (not self.shared_session or self.client.active or self.pending_mode)) or self.language.executing:
            raise ValueError("시뮬레이션 상태에서만 추종 설정을 적용할 수 있습니다.")
        if (not .25 <= perception <= .5 or not .3 <= hold <= 1.5 or
                type(frames) is not int or not 2 <= frames <= 8 or
                not max(.85, self.cfg["follow"]["desired_height_ratio"]) <= max_height <= .98 or
                type(image_size) is not int or image_size not in (320, 416, 480, 640)):
            raise ValueError("추종 설정 값이 허용 범위를 벗어났습니다.")
        self.cfg["safety"]["perception_timeout_s"] = perception
        self.cfg["gesture"].update(hold_s=hold, min_frames=frames, max_gap_s=perception)
        self.cfg["follow"]["size_max"] = max_height
        self.cfg["vision"]["image_size"] = image_size
        if self.shared_session:
            self.shared_session.cfg['safety']['perception_timeout_s'] = perception
            self.shared_session.stop('TRACKING_SETTINGS_CHANGED')
        self.c.halt("PAUSED", "TRACKING_SETTINGS_CHANGED", True)
        self.pending = None

    def connect_hand_drive(self, host, *, confidence, frames, hold, mapping):
        if self.source != "LIVE_CAMERA" or self.client or self.language.executing:
            raise ValueError("다른 제어를 정지하고 연결을 해제하세요. 합성 미리보기에서는 실물 제어를 사용할 수 없습니다.")
        hand = HandDrive(confidence, frames, hold, mapping)
        config = copy.deepcopy(self.cfg)
        config['communication'].update(host=host, signs_verified=True)
        client = (self.client_factory or HandDriveClient)(config)
        client.configure_owner('GESTURE_DIRECT', self._previous_session(host))
        client.start()
        self.language_authorized_host = None
        self.client, self.client_owner = client, 'GESTURE_DIRECT'
        self.hand, self.hand_armed_once = hand, False
        self.hand_requires_activation = False
        self.hand_connected_at = client.clock()
        self.c.hardware = True
        self.c.estop_latched = False
        self.c.halt('PAUSED', 'MODE_CHANGED_REARM_REQUIRED', True)
        self.pending = None
        self.notice = '손동작 직접 구동 연결 중. 정지 응답과 중립 손동작을 확인합니다.'
        if self.logger:
            self.logger.mode = "HARDWARE"
            self.logger.write(dict(event="MODE_CHANGED", active_control_mode=self.client_owner,
                                   controller_owner=self.client_owner, hand_mapping=mapping), True)

    def enable_direct(self):
        self._manual_client().enable_direct()
        self.c.estop_latched = False
        self.pending = None

    @control_locked
    def begin_hold(self, direction, *, timed_trial=False):
        if self.shared_session:
            if self.pending_mode or self.release_future:
                raise ValueError('기능 전환이 끝날 때까지 기다리세요.')
            if timed_trial:
                self.shared_session.begin_hold(direction, timed_trial=True)
            else:
                self.shared_session.begin_hold(direction)
            self.c.estop_latched = False
            self.pending = None
        else:
            if timed_trial:
                raise ValueError('실물 바퀴 시험을 먼저 선택하세요.')
            self._manual_client().begin_hold(direction)

    @control_locked
    def end_hold(self):
        if self.shared_session:
            self.shared_session.end_hold()
        elif self.client_owner == 'MANUAL_MOTOR_TEST':
            self.client.end_hold()

    def _manual_client(self):
        if not self.client or not getattr(self.client,'manual_test_only',False):
            raise ValueError('Connect the MOTOR TEST UDP controller first')
        return self.client

    def authorize_motor_test(self,lifted,approved):
        client=self._manual_client()
        client.authorize_manual(lifted,approved)
        self.c.estop_latched=False
        self.c.halt('PAUSED','MANUAL_MOTOR_TEST_ONLY',True)

    def begin_motor_test(self,direction,duration):
        self._manual_client().begin_test(direction,duration)

    @control_locked
    def resume_follow(self):
        if self.client is None and not self.language.executing:
            self.c.confirm_target()
            self.c.armed = True
            self.c.estop_latched = False
            self.request('RESUME')
            return
        client = self.shared_session
        if not client or self.client_owner != 'GESTURE_FOLLOW' or self.pending_mode or self.release_future:
            raise ValueError('사람 추종 기능과 연결 상태를 확인하세요.')
        if self.c.target_id is None:
            raise ValueError('카메라에서 추종할 사람을 선택하세요.')
        if not client.arm():
            raise ValueError('ESP32 정지 응답을 기다리세요.')
        self.c.confirm_target()
        self.c.armed = True
        self.c.estop_latched = False
        self.follow_auto_arm = False
        self.request('RESUME')

    @control_locked
    def start_natural_motion(self, host, prepared, *, validated_plan=None, session_guard=None, plan_ref=None):
        if self.source != 'LIVE_CAMERA':
            raise ValueError('합성 미리보기에서 실제 장치 실행은 차단됩니다.')
        if self.shared_session:
            radar = self._sync_radar(self.shared_session.clock())
            if radar and radar['blocked']:
                raise ValueError(radar['reason'])
            if (self.client_owner != 'LLM_COMMAND' or self.pending_mode or self.release_future or
                self.shared_session.host != host or not self.shared_session.snapshot()['stop_verified']):
                raise ValueError('자연어 제어로 전환하고 ESP32 정지 응답을 확인하세요.')
            if validated_plan is not None:
                if not self.central_ui or session_guard is None or self.c.estop_latched:
                    raise ValueError('Agent 실행 승인과 정지 상태를 확인하세요.')
                self.language.start_motion(host, prepared, session=self.shared_session,
                                           validated_plan=validated_plan, session_guard=session_guard, plan_ref=plan_ref)
            else:
                self.language.start_motion(host, True, session=self.shared_session)
            self.c.estop_latched = False
            self.pending = None
            return
        if validated_plan is not None:
            raise ValueError('기존 중앙 제어 세션이 필요합니다.')
        if self.client:
            raise ValueError('모터 동작 시험의 UDP 연결을 먼저 해제하세요.')
        self.language.previous = self._previous_session(host)
        self.language.start_motion(host, prepared is True or host == self.language_authorized_host)
        self.language_authorized_host = host
        self.c.estop_latched = False
        self.c.halt('PAUSED', 'NATURAL_LANGUAGE_CONTROL', True)
        self.pending = None

    def _drain_motor_events(self):
        if self.client and hasattr(self.client,'drain_transport_events'):
            for event in self.client.drain_transport_events():
                if self.logger:self.logger.write(event,True)
        if self.client and (getattr(self.client,'manual_test_only',False) is True or self.shared_session):
            for event in self.client.drain_events():
                if self.logger:self.logger.write(event,True)

    def disconnect(self):
        self.language_authorized_host = None
        if self.language.executing:
            self.language.cancel()
        if self.release_future:
            wait((self.release_future,))
            self.finish_disconnect()
        if self.client:
            self.last_transport=self.client.snapshot() if hasattr(self.client,'snapshot') else None
            if hasattr(self.client, "stop_and_release"):
                self.last_release = self.client.stop_and_release()
                if self.logger:
                    self.logger.write(dict(event="CONTROL_RELEASE", **self.last_release), True)
            else:
                self.client.close()
            self._drain_motor_events()
            if getattr(self.client,'manual_test_only',False):
                self.last_motor_snapshot=self.client.snapshot().get('motor_test')
        self._clear_connection()

    @control_locked
    def disconnect_async(self):
        self.language_authorized_host = None
        self.follow_auto_arm = False
        if self.language.executing:
            self.language.cancel()
        if not self.client or self.release_future:
            return
        client = self.client
        client.stop("CONTROL_RELEASE")
        self.last_transport = client.snapshot()
        future = self.release_future = Future()

        def release():
            try:
                future.set_result(client.stop_and_release())
            except Exception as exc:
                future.set_exception(exc)

        threading.Thread(target=release, name='dashboard-control-release', daemon=True).start()

    @control_locked
    def finish_disconnect(self):
        if self.release_future is None or not self.release_future.done():
            return False
        future, self.release_future = self.release_future, None
        try:
            self.last_release = future.result()
        except Exception as exc:
            self.last_release = None
            self.notice = f'송신기 종료 확인 실패 · {type(exc).__name__}'
            return False
        self._drain_motor_events()
        if getattr(self.client,'manual_test_only',False):
            self.last_motor_snapshot=self.client.snapshot().get('motor_test')
        self._clear_connection()
        if self.logger:
            self.logger.write(dict(event="CONTROL_RELEASE", **self.last_release), True)
        return True

    def _clear_connection(self):
        self.pending_mode = None
        self.client = self.client_owner = None
        self.c.hardware=False
        self.c.halt('PAUSED','MODE_CHANGED_REARM_REQUIRED',True)
        if self.logger: self.logger.mode='SIMULATION'
        self.notice=('이전 제어기의 정지 응답을 확인하지 못했습니다.'
                     if self.last_release and not self.last_release["stop_verified"]
                     else '시뮬레이션 상태입니다.')

    @control_locked
    def request(self,action,target_id=None):
        if action in ('ESTOP','PAUSE'):
            self.control_generation += 1
        if action=='PAUSE' and self.shared_session:
            self.shared_session.stop('USER_PAUSE')
        if action == 'ESTOP' or (action == 'PAUSE' and self.language.executing):
            self.language.cancel('EMERGENCY_STOP' if action == 'ESTOP' else 'USER_PAUSE')
        if action=='ESTOP':
            self.pending_mode = None
            self.language_authorized_host = None
            self.hand_armed_once = True
            self.follow_auto_arm = False
            if self.client: self.client.stop('EMERGENCY_STOP')
            self.c.estop_latched=True
            self.c.halt('PAUSED','EMERGENCY_STOP',True)
            self.pending=('ESTOP',None)
        elif action=='PAUSE' and self.client and getattr(self.client,'manual_test_only',False):
            self.client.stop('USER_STOP');self.pending=('PAUSE',None)
        elif self.pending is None or self.pending[0]!='ESTOP':
            self.pending=(action,target_id)

    @control_locked
    def tick(self,result,now=None,pipeline_error=None,pipeline_health=None):
        now=time.monotonic() if now is None else now
        radar = self._sync_radar(now)
        was_executing=self.language.executing
        self.language.poll()
        self._take_language_release()
        self._finish_mode_change()
        if was_executing and self.language.phase == 'FAILED':
            self.language_authorized_host = None
        previous_state=self.snapshot['state'] if self.snapshot else None
        if result is not None: self.last_result=result
        r=self.last_result
        f=r['frame'] if r else PerceptionFrame(now,640,480,(),camera_ok=False)
        if pipeline_error:
            f=PerceptionFrame(f.timestamp,f.width,f.height,f.people,f.gesture,False)
        action,target=self.pending or (None,None)
        self.pending=None
        if self.language.executing and action != 'ESTOP':
            action = 'PAUSE'
        if action=='SELECT': self.c.select_target(target);action=None
        elif action=='CLEAR': self.c.select_target(None);action='PAUSE'
        elif action=='CONFIRM': self.c.confirm_target();action=None
        if self.c.target_id is None:
            candidate=TargetSelector.candidate(f.people)
            if candidate is not None: self.c.select_target(candidate)
        transport=self.client.snapshot() if self.client else dict(connected=False,ready=False,armed=False,fault=None,reason='SIMULATION_NO_UDP',
            telemetry=None,telemetry_valid=False,last_rx_at=None,telemetry_age_s=None,last_tx=None,last_tx_at=None,
            tx_error=None,acknowledged_command=None,stop_verified=False,stop_requested_at=None,stop_seq=None)
        manual=bool(self.client and getattr(self.client,'manual_test_only',False))
        hand_direct=self.client_owner == 'GESTURE_DIRECT'
        shared = self.shared_session
        if shared:
            shared.controller_alive()
            if self.control_thread is None:
                shared.operator_alive()
            if shared.recovery_generation != self.seen_recovery_generation:
                self.seen_recovery_generation = shared.recovery_generation
                self.language.cancel()
                self.pending_mode = None
                self.pending = None
                action = None
                self.hand_armed_once = True
                self.follow_auto_arm = False
                self.c.halt('PAUSED','SESSION_RECOVERY',True)
        if manual:
            if not self.error:self.client.operator_alive()
            if action in ('ARM','RESUME'):
                self.notice='AUTOMATIC_HARDWARE_FOLLOWING_DISABLED: use separate MOTOR TEST authorization'
                action=None
        if (self.client_owner == 'GESTURE_FOLLOW' and self.follow_auto_arm and self.c.confirmed and not self.c.estop_latched and
            not self.release_future and f.camera_ok and 0 <= now-f.timestamp <= self.cfg['safety']['perception_timeout_s'] and
            (f.gesture is None or f.gesture.name != 'Open_Palm') and action is None and transport['stop_verified']):
            action='ARM'
        if hand_direct and action == 'PAUSE':
            self.hand_armed_once = True
            if not shared:
                self.client.stop('USER_STOP')
        if action=='ARM' and self.client and not hand_direct and (self.release_future or not self.client.arm()):
            action=None
            self.notice='ARM_DENIED: fresh, matching STOPPED telemetry required'
        if self.client: transport=self.client.snapshot()
        comm_ok=not self.client or (transport['connected'] and (not self.c.armed or transport['armed']))
        decision=self.c.step(f,now,action if not manual or action=='ESTOP' else 'PAUSE',comm_ok,transport['fault'] if self.client else 'NONE')
        if self.client_owner == 'GESTURE_FOLLOW' and self.c.armed:
            self.follow_auto_arm = False
        if self.client and not hand_direct and not self.release_future:
            if self.client_owner == "GESTURE_FOLLOW" and transport["armed"] and not self.c.armed:
                self.client.stop(decision.reason)
            if self.client_owner == 'GESTURE_FOLLOW':
                self.client.submit(decision, f.timestamp, r.get('completed_at') if r else None)
            elif not shared:
                self.client.submit(decision,f.timestamp)
        target=next((p for p in f.people if p.track_id==self.c.target_id),None)
        fresh=bool(f.camera_ok and 0<=now-f.timestamp<=self.cfg['safety']['perception_timeout_s'])
        vision=copy.deepcopy(r['vision']) if r else gesture_diagnostics([],self.c.target_id,0,now,0)
        hand_status = None
        hand_applied = None
        if hand_direct:
            valid = bool(r and fresh and r.get('hand_only') and f.timestamp >= self.hand_connected_at
                         and not self.c.estop_latched and not self.hand_requires_activation
                         and self.release_future is None)
            hand_status = self.hand.observe(vision, f.timestamp, now, self.cfg['safety']['perception_timeout_s'], valid)
            neutral = vision['hand_count'] == 0 or hand_status.get('label') == 'Closed_Fist'
            if valid and neutral and not (radar and radar['blocked']):
                self.radar_neutral_required = False
            if (shared and valid and not self.hand_armed_once and not self.radar_neutral_required and
                transport['stop_verified'] and not self.pending_mode):
                self.hand_armed_once = shared.arm()
            if not valid and transport['armed']:
                self.client.stop('PERCEPTION_STALE_OR_FAILED')
            if (valid and not self.hand_armed_once and
                (vision['hand_count'] == 0 or hand_status.get('label') == 'Closed_Fist') and self.client.arm()):
                self.hand_armed_once = True
                self.hand.reset()
            if transport['armed'] and valid:
                hand_applied = self.client.submit_gesture(hand_status, f.timestamp, r['completed_at'])
                if hand_status['direction'] != 'STOP' and hand_applied == MotionCommand():
                    hand_status.update(reason='WAITING_FOR_STOP', direction='STOP')
            elif not transport['armed']:
                reason = transport['validation_detail'] if not transport.get('connected') else (
                    transport['reason'] if self.hand_armed_once else 'WAITING_FOR_NEUTRAL_HAND')
                hand_status = {**hand_status, 'direction': 'STOP', 'reason': reason}
        gesture=dict(self.c.gesture_status)
        if vision.get('rejection'): gesture['rejection']=vision['rejection']
        if not fresh: gesture.update(confirmed=None,progress=0,rejection='PERCEPTION_STALE_OR_FAILED')
        if self.c.state in ('FAULT','TARGET_LOST','OBSTACLE_STOP') or self.c.estop_latched:
            gesture['rejection']='SAFETY_STOP_ACTIVE' if not vision.get('rejection') else vision['rejection']
        telemetry=transport['telemetry']
        requested=asdict(self.c.requested_command) if self.c.requested_command else None
        safe=asdict(decision.command)
        motor=transport.get('motor_test') or self.last_motor_snapshot
        if manual:
            gesture.update(applied_action=None,rejection='MANUAL_MOTOR_TEST_ONLY')
            safe=transport['motor_test']['command']
            requested=safe.copy()
        center=((target.bbox[0]+target.bbox[2])/2,(target.bbox[1]+target.bbox[3])/2) if target else None
        data=dict(state=decision.state,stop_reason=decision.reason,target_id=self.c.target_id,
            armed=self.c.armed,following=decision.state in ('FOLLOWING','GESTURE_OVERRIDE'),
            target_confirmed=self.c.confirmed,target_lost=decision.state=='TARGET_LOST',
            detected_people=len(f.people),detection_confidence=target.confidence if target else None,
            target_last_seen=self.c.previous.timestamp if self.c.previous else None,target_center=center,
            horizontal_error=(center[0]-f.width/2)/(f.width/2) if center else None,
            relative_size=(target.bbox[3]-target.bbox[1])/f.height if target else None,
            desired_height_ratio=self.c.follow.desired,requested=requested,safe=safe,
            limits_or_stop_applied=requested is not None and requested!=safe,vision=vision,gesture=gesture,
            raw_gesture=vision['raw'],confirmed_gesture=gesture.get('confirmed'),applied_action=gesture.get('applied_action'),
            gesture_rejection_reason=gesture.get('rejection'),transport=transport,
            telemetry=telemetry,motors=motor_feedback(telemetry,self.cfg['communication']['wheel_radius_cm']),
            fps=r['fps'] if r else None,processing_ms=r['latency_ms'] if r else None,
            vision_metrics=r.get('metrics', {}) if r else {}, hand_control=hand_status,
            frame_age_s=max(0,now-f.timestamp) if r else None,perception_fresh=fresh,
            frame_at=f.timestamp if r else None,vision_completed_at=r['completed_at'] if r else None,
            program_mode=self.mode,evidence_source=self.source,notice=pipeline_error or self.notice,pipeline_health=pipeline_health,
            control_source=self.client_owner or ('LLM_COMMAND' if self.language.executing else 'VISION_SIMULATION'),
            active_control_mode=self.active_control_mode,controller_owner=self.active_control_mode,
            motor_test=motor,radar=radar)
        if manual:
            data.update(state='MOTOR_TEST_'+motor['status'],stop_reason=transport['reason'],armed=transport['manual_enabled'],following=False)
        if hand_direct:
            data.update(state='GESTURE_DIRECT',stop_reason=hand_status['reason'],armed=transport['armed'],following=False,
                        requested=None,safe=asdict(hand_applied or MotionCommand()),limits_or_stop_applied=False)
        if shared and self.client_owner in ('DIRECT_CONTROL', 'MOTOR_TEST', 'LLM_COMMAND'):
            data.update(state=self.client_owner, stop_reason=transport['reason'], armed=transport['armed'], following=False,
                        requested=None,safe=transport['body_command'], limits_or_stop_applied=False)
        self.snapshot=data
        if self.client:self.last_transport=transport
        if self.logger:
            try:
                self.logger.mode=self.mode
                self._drain_motor_events()
                self.logger.write(data,force=action in ('ESTOP','ARM','RESUME','PAUSE') or bool(gesture.get('applied_action')) or data['state']!=previous_state)
            except Exception as exc:
                self.error=f'LOGGING_FAILED: {type(exc).__name__}'
                self.request('ESTOP')
        return data

    def close(self):
        self.control_done.set()
        if self.control_thread is not None and threading.current_thread() is not self.control_thread:
            self.control_thread.join(timeout=1)
        self.language.close()
        if self.client:
            self.disconnect()
        if self.radar_receiver:
            self.radar_receiver.close()
        if self.logger:
            self.logger.save_tests(self.book)
            self.logger.close()
