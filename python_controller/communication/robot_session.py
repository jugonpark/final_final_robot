"""One UDP connection and command sequence shared by the central dashboard."""
from dataclasses import asdict
from concurrent.futures import Future
import json
import math
import socket
import threading
import time
from queue import Queue, Empty, Full

from python_controller.communication.esp32_udp import UdpClient, TelemetryGuard, uint, number
from python_controller.communication.motor_test import MotorTestClient, body_test_command
from python_controller.communication.session_sync import session_fields, SessionSync
from python_controller.communication.timing import CONTROLLER_LEASE_S, HEARTBEAT_PERIOD_S, firmware_timing
from python_controller.config import HARDWARE_PROFILES
from python_controller.control.hand_drive import HandDriveClient
from python_controller.diagnostics import safe_payload
from python_controller.models import MotionCommand


CONTROL_MODES = ('DIRECT_CONTROL', 'GESTURE_DIRECT', 'GESTURE_FOLLOW', 'LLM_COMMAND', 'MOTOR_TEST')
BENCH_LINEAR_LIMIT = 1.0  # cm/s
BENCH_ANGULAR_LIMIT = .10  # rad/s
BENCH_HOLD_SECONDS = 2.0
WEB_HOLD_SECONDS = .25
RADAR_NOT_SEEN = ('RADAR_NO_DATA', 'RADAR_NOT_CONFIGURED')
PROFILE_FIELDS = ('hardware_profile', 'firmware_version', 'pinmap_revision')


class RobotSession(MotorTestClient, HandDriveClient):
    central_session = True
    requires_verified_signs = False
    manual_test_only = False

    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)
        self.owner = 'DIRECT_CONTROL'
        self.operator_at = -math.inf
        self.distance_leased = False
        self.distance_cancelled = False
        self.distance_finished = threading.Event()
        self.distance_finished.set()
        self.guard.allowed_states = (*self.guard.allowed_states, 'DISTANCE_ACTIVE', 'DISTANCE_BRAKING')
        self.cfg['communication']['send_period_s'] = .02
        self.controller_at = self.execution_at = -math.inf
        self.requests = Queue(maxsize=32)
        self.distance_channel = None
        self.execution_token = 0
        self.active_motion_id = None
        self.last_heartbeat_at = self.last_velocity_at = -math.inf
        self.first_velocity_seq = None
        self.heartbeat_gap_ms = None
        self.recovery_state = 'NONE'
        self.recovery_generation = 0
        self.retired_boots = set()
        self.stop_pending = True
        self.v2_barrier = None
        self.v2_negotiating = False
        self.safety = None
        self.radar_generation = -1
        self.distance_radar_generation = None
        self.hardware_profile = config['communication'].get('hardware_profile')
        self.profile_required = self.hardware_profile is not None
        self.profile_declared = False
        self.radarless_bench = config['communication'].get('radarless_bench') is True
        self.bench_authorized = False
        self.bench_hold_until = -math.inf
        self.web_hold_until = None

    def start(self):
        if self.safety is None and self.socket_factory is socket.socket:
            raise ValueError('RADAR_DASHBOARD_REQUIRED')
        self.profile_required = self.profile_required or self.socket_factory is socket.socket
        super().start()

    def _profile_status(self, now):
        if not self.profile_required:
            return 'NOT_REQUIRED'
        if self.hardware_profile not in HARDWARE_PROFILES:
            return 'HARDWARE_PROFILE_UNSELECTED'
        telemetry = self.guard.data or {}
        if not self.guard.link_fresh(now):
            return 'HARDWARE_PROFILE_UNKNOWN'
        if (self.hardware_profile == 'BREADBOARD' and not self.profile_declared
                and not any(key in telemetry for key in PROFILE_FIELDS)):
            return 'LEGACY_BREADBOARD'
        identity = tuple(telemetry.get(key) for key in PROFILE_FIELDS)
        if not all(isinstance(value, str) and value for value in identity):
            return 'HARDWARE_PROFILE_UNKNOWN'
        if identity != (self.hardware_profile, 'SIMPLE_V2', f'{self.hardware_profile}_GPIO_V1'):
            return 'HARDWARE_PROFILE_MISMATCH'
        return 'MATCH'

    def _profile_block(self, now):
        status = self._profile_status(now)
        return status if status.startswith('HARDWARE_PROFILE_') else None

    def radar_status(self):
        if self.safety is None:
            return None
        status = self.safety.snapshot(self.clock())
        if self.bench_authorized and status['boot_id'] is None and status['reason'] in RADAR_NOT_SEEN:
            return dict(status, blocked=False, reason='RADARLESS_BENCH')
        return status

    def authorize_bench(self):
        with self.lock:
            status = self.snapshot()
            if not status['bench_available']:
                raise ValueError('레이더 미수신, 보드 프로필 일치와 ESP32 정지 확인이 필요합니다.')
            self.bench_authorized = True

    def _apply_radar(self):
        status = self.radar_status()
        if self.bench_authorized and status and status['reason'] != 'RADARLESS_BENCH':
            self.radar_generation = status['generation']
            self.stop('RADARLESS_BENCH_RADAR_CHANGED')
        elif status and status['blocked'] and (
            status['generation'] != self.radar_generation or self.active or self.armed
        ):
            self.radar_generation = status['generation']
            self.stop(status['reason'])
        return status

    def rearm_radar(self, *, after_evade=False):
        with self.lock:
            if self.safety is None:
                raise ValueError('레이더를 사용하는 중앙 제어 연결이 필요합니다.')
            with self.safety.lock:
                radar = self._apply_radar()
                status = self.snapshot()
                if self.distance_leased or not status['stop_verified']:
                    raise ValueError('ESP32의 새 STOP 응답과 실행 종료를 확인하세요.')
                if after_evade and (radar['reason'] != 'RADAR_EVADE' or not status['ready']):
                    raise ValueError('위험 정지 이후의 레이더와 세션 상태를 확인하세요.')
                self.safety.rearm(self.clock())
                self.distance_cancelled = False
                self.reason = 'RADAR_CLEAR'

    def operator_alive(self):
        with self.lock:
            self.operator_at = self.clock()
            if self.thread is None:
                self.controller_at = self.clock()

    def controller_alive(self):
        with self.lock:
            self.controller_at = self.clock()

    def execution_alive(self):
        with self.lock:
            self.execution_at = self.clock()

    def arm(self):
        with self.lock:
            status = self._apply_radar()
            if self.radarless_bench and not self.bench_authorized:
                return False
            return not (status and status['blocked']) and not self._profile_block(self.clock()) and UdpClient.arm(self)

    def submit(self, decision, frame_timestamp, recognized_at=None):
        with self.lock:
            if self.radarless_bench:
                return
            status = self._apply_radar()
            if status and status['blocked']:
                return
            was_active = self.active
            UdpClient.submit(self,decision,frame_timestamp,recognized_at)
            if self.active and not was_active:
                self.execution_token += 1

    def select_mode(self, mode):
        with self.lock:
            if mode not in CONTROL_MODES:
                raise ValueError('지원하지 않는 제어 기능입니다.')
            if self.radarless_bench and mode != 'DIRECT_CONTROL':
                raise ValueError('레이더 미수신 벤치 시험은 누르고 있는 방향 입력만 허용합니다.')
            status = self.snapshot()
            if self.distance_leased or not status['ready'] or not status['stop_verified']:
                raise ValueError('현재 명령의 정지 응답을 기다리는 중입니다.')
            self.owner = mode
            self.allow_all_axes = mode != 'GESTURE_FOLLOW'
            self.reason = 'MODE_READY'
            self.distance_cancelled = False
            self.armed = self.active = False
            self.active_motion_id = None

    def begin_hold(self, direction, *, timed_trial=False, remote=False):
        with self.lock:
            if remote and (self.radarless_bench or timed_trial or self.owner not in ('DIRECT_CONTROL', 'MOTOR_TEST')):
                raise ValueError('웹의 방향 입력에는 일반 수동 제어권이 필요합니다.')
            if self.radarless_bench and (not self.bench_authorized or timed_trial or self.owner != 'DIRECT_CONTROL'):
                raise ValueError('바퀴 고정을 확인하고 벤치 방향 버튼을 누르세요.')
            if timed_trial and self.owner != 'MOTOR_TEST':
                raise ValueError('시간 제한 시험은 바퀴 시험 화면에서 실행하세요.')
            if self.active or self.trial is not None:
                raise ValueError('현재 시험의 종료를 기다리세요.')
            if self.owner not in ('DIRECT_CONTROL', 'MOTOR_TEST') or self.distance_leased:
                raise ValueError('직접 제어 또는 바퀴 시험 기능을 선택하세요.')
            if direction in ('M1', 'M2', 'M3') and self.owner != 'MOTOR_TEST':
                raise ValueError('바퀴별 명령은 테스트 화면에서 실행하세요.')
            if not self.arm():
                raise ValueError('ESP32의 정상 연결과 정지 응답을 확인하세요.')
            if not 0 <= self.clock()-self.operator_at <= self.cfg['motor_test']['ui_lease_s']:
                self.stop('OPERATOR_LEASE_EXPIRED')
                raise ValueError('화면 갱신을 확인하세요.')
            self.execution_token += 1
            self.last_velocity_at = -math.inf
            if self.owner == 'MOTOR_TEST':
                self.authorize_manual(True, True)
                self.begin_test(direction, duration=.5 if timed_trial else None, held=not timed_trial)
                if remote:
                    self.web_hold_until = self.clock() + WEB_HOLD_SECONDS
                return
            config = self.cfg['motor_test']
            if self.radarless_bench:
                config = dict(config, linear_cm_s=min(config['linear_cm_s'], BENCH_LINEAR_LIMIT),
                              angular_rad_s=min(config['angular_rad_s'], BENCH_ANGULAR_LIMIT))
                self.bench_hold_until = self.clock() + BENCH_HOLD_SECONDS
            self.command = body_test_command(direction, config)
            if remote:
                self.web_hold_until = self.clock() + WEB_HOLD_SECONDS
            self.active = True
            self.reason = direction

    def renew_web_hold(self):
        with self.lock:
            now = self.clock()
            if not self.active or self.web_hold_until is None or now >= self.web_hold_until:
                raise ValueError('웹 수동 입력이 종료됐습니다. 방향 버튼을 다시 누르세요.')
            self.web_hold_until = now + WEB_HOLD_SECONDS

    def release_web_hold(self):
        with self.lock:
            if self.web_hold_until is not None:
                self.stop('HOLD_RELEASED')

    def end_hold(self):
        self.stop('HOLD_RELEASED')

    def _latch(self, reason):
        self.web_hold_until = None
        self.bench_hold_until = -math.inf
        if reason != 'HOLD_RELEASED':
            self.bench_authorized = False
        if self.distance_leased:
            self.distance_cancelled = True
        super()._latch(reason)
        if self.stop_seq is None:
            self.stop_pending = True

    def _accept_transport_packet(self, data, now):
        if not isinstance(data,dict):
            return True
        if (data.get('session_id') != self.protocol.session_id and self.session_sync and
                self.session_sync.delayed_previous_stop(data, self.guard.data)):
            self.rx_rejections['DELAYED_PREVIOUS_STOP'] = self.rx_rejections.get('DELAYED_PREVIOUS_STOP', 0) + 1
            return False
        boot = data.get('boot_id')
        if boot in self.retired_boots:
            self.rx_rejections['RETIRED_BOOT'] = self.rx_rejections.get('RETIRED_BOOT',0)+1
            return False
        if (uint(boot) and boot and self.guard.boot_id is not None and boot != self.guard.boot_id and
            data.get('session_id') == self.protocol.session_id and data.get('mode') == 'NETWORK'):
            self._start_recovery(boot)
        return True

    def _start_recovery(self, boot=None):
        old_boot = self.guard.boot_id
        if boot is not None and old_boot is not None and old_boot != boot:
            self.retired_boots.add(old_boot)
        self.stop('ESP32_REBOOT' if boot is not None else 'SESSION_RECOVERY')
        self.session_lost = True
        self.recovery_state = 'SYNC_STOP'
        self.recovery_generation += 1
        self.session_sync = SessionSync()
        allowed = self.guard.allowed_states
        self.guard = TelemetryGuard(self.protocol.session_id,self.host,self.cfg['safety']['telemetry_timeout_s'])
        self.guard.allowed_states = allowed
        self.guard.boot_id = boot if boot is not None else old_boot
        self.guard.stop_barrier = self.protocol.seq+1
        self.sync_deadline = self.clock()+2
        self.execution_token += 1
        self.active_motion_id = None
        self.v2_barrier = None
        self.v2_negotiating = False

    def recover(self):
        with self.lock:
            if self.distance_leased:
                self.stop('SESSION_RECOVERY')
                raise ValueError('현재 실행 중단 후 다시 동기화하세요.')
            data = self.guard.data or {}
            reset_encoder = data.get('fault') in ('ENCODER_JUMP','ENCODER_INVALID')
            if reset_encoder:
                if self._timing()[0] != 2:
                    raise ValueError('현재 펌웨어는 신호 회복을 확인하는 복구 기능을 지원하지 않습니다.')
                if not self.snapshot()['stop_verified'] or data.get('encoder_good_samples',0) < 10:
                    raise ValueError('출력 정지와 엔코더 정상 샘플 10개를 확인한 후 복구하세요.')
            self._start_recovery()
            if reset_encoder:
                self.requests.put_nowait((dict(type='reset_fault'),Future(),self.recovery_generation,None))

    def _on_telemetry(self, data, now):
        if any(key in data for key in PROFILE_FIELDS):
            self.profile_declared = True
        super()._on_telemetry(data, now)

    def _on_transport_packet(self, packet, source, now, accepted):
        if accepted:
            self._apply_profile(now)
        if self.distance_channel is not None:
            self.distance_channel.publish(packet,source)
        telemetry = self.guard.data or {}
        if (accepted and self.active and not self.distance_leased and self.first_velocity_seq is not None and
            telemetry.get('protocol_version') == 2 and telemetry.get('state') == 'STOPPED' and
            telemetry.get('execution_token') == self.execution_token and telemetry.get('motion_permit') is False and
            telemetry['last_seq'] >= self.first_velocity_seq):
            self.stop('DEVICE_STOPPED_DURING_HOLD')
        if accepted and self._timing()[0] == 2 and not self.v2_negotiating:
            self.guard.timeout = .8
            self.v2_negotiating = True
            self.stop_seq = None
            self.stop_requested_at = now
            self.stop_pending = True
        if (self.recovery_state == 'SYNC_STOP' and accepted and self.snapshot()['stop_verified'] and
            self.guard.ready(now)):
            self.session_lost = False
            self.recovery_state = 'RECOVERED_WAITING_NEW_INPUT'
            self.session_diagnostic = None
            self.reason = 'RECOVERED_WAITING_NEW_INPUT'
        elif self.session_lost and self.recovery_state != 'SYNC_STOP':
            self.recovery_state = 'SESSION_CONFLICT'

    def _timing(self):
        try:
            return firmware_timing(self.guard.data or {})
        except ValueError:
            return 1,300,300

    def _controller_healthy(self, now):
        return 0 <= now-self.controller_at <= CONTROLLER_LEASE_S

    def _stop_packet(self):
        return self.protocol.packet('stop',protocol_version=2) if self._timing()[0] == 2 else self.protocol.stop()

    def _heartbeat_packet(self, now):
        if self._timing()[0] == 1:
            return self.protocol.heartbeat()
        healthy = self._controller_healthy(now)
        permit = healthy and self.active and not self.distance_cancelled and not self.session_lost
        if self.distance_leased:
            permit = permit and 0 <= now-self.execution_at <= CONTROLLER_LEASE_S
        peer_active = (self.guard.data or {}).get('state') in ('VELOCITY','DISTANCE_ACTIVE','DISTANCE_BRAKING')
        token = self.execution_token if permit or peer_active else 0
        return self.protocol.packet('heartbeat',protocol_version=2,controller_alive=healthy,
                                    motion_permit=bool(permit),motion_id=self.active_motion_id,
                                    execution_token=token)

    def _motion_packet(self, kind, fields):
        if self._timing()[0] == 2:
            fields.update(protocol_version=2,execution_token=self.execution_token)
        return self.protocol.packet(kind,**fields)

    def _service_request(self, now):
        try:
            command, future, generation, channel = self.requests.get_nowait()
        except Empty:
            return False
        if not future.set_running_or_notify_cancel():
            return False
        kind = command.pop('type')
        radar = self.radar_status()
        if (kind not in ('stop','heartbeat','cmd_move','set_test_profile','reset_fault') or
            generation != self.recovery_generation or
            (kind == 'cmd_move' and radar and
                (radar['blocked'] or radar['generation'] != self.distance_radar_generation)) or
            (kind in ('heartbeat','cmd_move') and
             (channel is None or channel is not self.distance_channel or self.distance_cancelled or self.session_lost))):
            future.set_exception(OSError('CONTROL_CANCELLED'))
            return False
        try:
            if kind in ('heartbeat', 'cmd_move') and (blocked := self._profile_block(now)):
                raise OSError(blocked)
            if kind == 'heartbeat':
                packet = self._heartbeat_packet(now)
            elif kind == 'cmd_move':
                if not self.distance_leased:
                    raise OSError('EXECUTION_NOT_ACTIVE')
                if self.active_motion_id != command['motion_id']:
                    self.execution_token += 1
                self.active_motion_id = command['motion_id']
                self.active = True
                self.command = MotionCommand(command['vx'],command['vy'],command['w'])
                self.stop_seq = None
                self.stop_requested_at = now
                packet = self._motion_packet(kind,command)
            elif kind == 'stop':
                if self.active:
                    self.stop_seq = None
                    self.stop_requested_at = now
                self.active = False
                self.command = MotionCommand()
                packet = self._stop_packet()
            elif kind == 'reset_fault':
                packet = self.protocol.packet('reset_fault',protocol_version=2)
                self.stop_pending = True
            else:
                packet = self.protocol.packet(kind,protocol_version=2,**command)
            if self._send_packet(packet,kind == 'cmd_move') is False:
                raise OSError(self._profile_block(self.clock()) or 'RADAR_BLOCKED')
            future.set_result((json.loads(packet),self.last_tx_at))
        except (OSError,ValueError,KeyError) as exc:
            future.set_exception(exc)
        return True

    def _apply_profile(self, now):
        profile_block = self._profile_block(now)
        if profile_block and (self.armed or self.active or (self.distance_leased and not self.distance_cancelled)):
            self.stop(profile_block)
        return profile_block

    def _select_packet(self, now):
        radar = self._apply_radar()
        profile_block = self._apply_profile(now)
        if self.radarless_bench and self.active and now >= self.bench_hold_until:
            self.stop('RADARLESS_BENCH_HOLD_EXPIRED')
        if self.web_hold_until is not None and now >= self.web_hold_until:
            self.stop('WEB_HOLD_EXPIRED')
        if self.trial is not None:
            deadline = self.trial['deadline']
            if self.active and deadline is not None and now >= deadline:
                self.stop('TEST_DURATION_COMPLETE_REARM_REQUIRED')
            if self.trial['ended_at'] is not None:
                if self.snapshot()['stop_verified']:
                    self._finish(True)
                elif now - self.trial['ended_at'] >= self.cfg['motor_test']['stop_feedback_timeout_s']:
                    self._finish(False)
        if self.distance_leased and (not self._controller_healthy(now) or
                                    not 0 <= now-self.execution_at <= CONTROLLER_LEASE_S):
            self.stop('CONTROLLER_LEASE_EXPIRED')
        if self.stop_pending:
            return self._stop_packet(),False
        if self._service_request(now):
            return None,False
        if radar and radar['blocked']:
            if not self.snapshot()['stop_verified'] and (
                self.last_tx_at is None or now-self.last_tx_at >= HEARTBEAT_PERIOD_S
            ):
                return self._stop_packet(),False
            return None,False
        if profile_block:
            if not self.snapshot()['stop_verified'] and (self.last_tx_at is None or now-self.last_tx_at >= HEARTBEAT_PERIOD_S):
                return self._stop_packet(),False
            return None,False
        if self.session_lost or self.distance_cancelled or self.guard.reason != 'OK':
            if self.last_tx_at is None or now-self.last_tx_at >= HEARTBEAT_PERIOD_S:
                return self._stop_packet(),False
            return None,False
        if self.distance_leased:
            if now-self.last_heartbeat_at >= HEARTBEAT_PERIOD_S:
                return self._heartbeat_packet(now),False
            return None,False
        if self.armed and not self._controller_healthy(now):
            self.stop('CONTROLLER_LEASE_EXPIRED')
        if self.armed and self.owner in ('DIRECT_CONTROL','MOTOR_TEST') and not 0 <= now-self.operator_at <= self.cfg['motor_test']['ui_lease_s']:
            self.stop('OPERATOR_LEASE_EXPIRED')
        if self.armed and self.owner in ('GESTURE_DIRECT','GESTURE_FOLLOW') and not 0 <= now-self.frame_timestamp <= self.cfg['safety']['perception_timeout_s']:
            self.stop('PERCEPTION_LEASE_EXPIRED')
        if self.armed and not self.guard.healthy(now):
            self.stop('TELEMETRY_STALE_OR_INVALID')
        cmd = self.command
        if self.armed and self.active and (
            not all(number(value) for value in (cmd.vx_cm_s,cmd.vy_cm_s,cmd.w_rad_s)) or
            math.hypot(cmd.vx_cm_s,cmd.vy_cm_s)>5 or abs(cmd.w_rad_s)>.5 or
            (self.owner=='GESTURE_FOLLOW' and (cmd.vx_cm_s*self.cfg['follow']['forward_sign']<0 or cmd.vy_cm_s!=0))):
            self.stop('INVALID_MOTION')
        if self.stop_pending:
            return self._stop_packet(),False
        if self.armed and self.active:
            if now-self.last_heartbeat_at >= HEARTBEAT_PERIOD_S:
                if self._send_packet(self._heartbeat_packet(now)) is False:
                    return None,False
            if now-self.last_velocity_at >= HEARTBEAT_PERIOD_S:
                fields = dict(vx=self.command.vx_cm_s,vy=self.command.vy_cm_s,w=self.command.w_rad_s,status='SLOW')
                return self._motion_packet('cmd_vel',fields),True
            return None,False
        if self.snapshot()['stop_verified']:
            if now-self.last_heartbeat_at >= HEARTBEAT_PERIOD_S:
                return self._heartbeat_packet(now),False
        elif self.last_tx_at is None or now-self.last_tx_at >= HEARTBEAT_PERIOD_S:
            return self._stop_packet(),False
        return None,False

    def _send_packet(self, packet, moving=False):
        command = json.loads(packet)
        if command['type'] not in ('cmd_vel', 'cmd_move') and command.get('motion_permit') is not True:
            return super()._send_packet(packet, moving)
        if blocked := self._profile_block(self.clock()):
            self._latch(blocked)
            super()._send_packet(self._stop_packet())
            return False
        if self.radarless_bench and not self._bench_packet_allowed(command):
            self._latch('RADARLESS_BENCH_HOLD_EXPIRED')
            super()._send_packet(self._stop_packet())
            return False
        if self.safety is None:
            return super()._send_packet(packet, moving)
        with self.safety.lock:
            if self.web_hold_until is not None and self.clock() >= self.web_hold_until:
                self._latch('WEB_HOLD_EXPIRED')
                super()._send_packet(self._stop_packet())
                return False
            if self._apply_radar()['blocked'] or (self.radarless_bench and not self.bench_authorized):
                super()._send_packet(self._stop_packet())
                return False
            return super()._send_packet(packet, moving)

    def _bench_packet_allowed(self, command):
        if (not self.bench_authorized or not self.armed or not self.active or self.owner != 'DIRECT_CONTROL'
                or self.safety is None or self.clock() >= self.bench_hold_until):
            return False
        if command['type'] == 'heartbeat':
            return True
        vx, vy, w = (command.get(axis) for axis in ('vx', 'vy', 'w'))
        return (command['type'] == 'cmd_vel' and all(number(value) for value in (vx, vy, w))
                and math.hypot(vx, vy) <= BENCH_LINEAR_LIMIT and abs(w) <= BENCH_ANGULAR_LIMIT)

    def _after_send(self, packet):
        super()._after_send(packet)
        if packet['type'] == 'stop':
            self.first_velocity_seq = None
            self.stop_pending = False
            if packet.get('protocol_version') == 2 and self.v2_barrier is None:
                self.v2_barrier = packet['seq']
                self.v2_negotiating = True
                self.guard.timeout = .8
        if packet['type'] == 'heartbeat':
            if math.isfinite(self.last_heartbeat_at):
                self.heartbeat_gap_ms = (self.last_tx_at-self.last_heartbeat_at)*1000
            self.last_heartbeat_at = self.last_tx_at
        if packet['type'] == 'cmd_vel':
            if self.first_velocity_seq is None:
                self.first_velocity_seq = packet['seq']
            self.last_velocity_at = self.last_tx_at

    def distance_socket(self):
        with self.lock:
            if self.radarless_bench:
                raise ValueError('레이더 미수신 벤치 시험은 계획 실행을 지원하지 않습니다.')
            radar = self._apply_radar()
            if radar and radar['blocked']:
                raise ValueError(radar['reason'])
            status = self.snapshot()
            if self.owner != 'LLM_COMMAND' or self.distance_leased or not status['ready'] or not status['stop_verified']:
                raise ValueError('자연어 제어의 정지 응답과 연결 상태를 확인하세요.')
            self.distance_leased = True
            self.distance_radar_generation = radar['generation'] if radar else None
            self.distance_cancelled = False
            self.distance_finished.clear()
            self.execution_token += 1
            self.active_motion_id = None
            self.execution_at = self.clock()
            self.armed = self.active = False
            self.command = MotionCommand()
            self.frame_timestamp = -math.inf
            self.recognized_at = None
            self.distance_channel = SessionChannel(self)
            return self.distance_channel

    def set_encoder_policy(self, policy):
        with self.lock:
            status = self.snapshot()
            if policy not in ('STRICT','FILTERED_TEST') or not status['supported_test_profile']:
                raise ValueError('현재 펌웨어가 엔코더 시험 정책을 지원하지 않습니다.')
            if self.active or self.distance_leased or not status['stop_verified']:
                raise ValueError('출력 정지 확인 후 시험 정책을 변경하세요.')
            future = Future()
            try:
                self.requests.put_nowait((dict(type='set_test_profile',encoder_policy=policy),future,self.recovery_generation,None))
            except Full as exc:
                raise ValueError('제어 요청 큐가 가득 찼습니다. 잠시 후 다시 시도하세요.') from exc
            return future

    def snapshot(self):
        with self.lock:
            data = super().snapshot()
            version,heartbeat,velocity = self._timing()
            telemetry = self.guard.data or {}
            profile_status = self._profile_status(self.clock())
            data.update(hardware_profile_expected=self.hardware_profile,
                        hardware_profile=telemetry.get('hardware_profile'),
                        firmware_version=telemetry.get('firmware_version'),
                        pinmap_revision=telemetry.get('pinmap_revision'),
                        hardware_profile_status=profile_status)
            if profile_status.startswith('HARDWARE_PROFILE_'):
                data['ready'] = False
            data.update(central_session=True,manual_test_only=False,controller_owner=self.owner,distance_leased=self.distance_leased,
                        body_command=asdict(self.command) if self.active else asdict(MotionCommand()))
            data.update(recovery_state=self.recovery_state,recovery_generation=self.recovery_generation,
                        radar=self.radar_status(),
                        protocol_version=version,heartbeat_timeout_ms=heartbeat,velocity_ttl_ms=velocity,
                        heartbeat_period_ms=100,controller_age_s=self.clock()-self.controller_at if math.isfinite(self.controller_at) else None,
                        heartbeat_gap_ms=self.heartbeat_gap_ms,encoder_policy=telemetry.get('encoder_policy'),
                        supported_test_profile=version==2 and telemetry.get('encoder_policy') in ('STRICT','FILTERED_TEST'))
            if version == 2 and (self.v2_barrier is None or telemetry.get('last_seq',-1) < self.v2_barrier):
                data.update(ready=False,stop_verified=False)
            radar = data['radar'] or {}
            data.update(radarless_bench=self.radarless_bench, bench_authorized=self.bench_authorized,
                        bench_available=bool(self.radarless_bench and not self.bench_authorized and
                                             self.owner == 'DIRECT_CONTROL' and not self.armed and not self.active and
                                             data['hardware_profile_status'] == 'MATCH' and data['ready'] and
                                             data['stop_verified'] and radar.get('boot_id') is None and
                                             radar.get('reason') in RADAR_NOT_SEEN))
            return data

    def stop_and_release(self, timeout=.85):
        self.stop('CONTROL_RELEASE')
        self.distance_finished.wait(timeout)
        return super().stop_and_release(timeout)


class SessionChannel:
    """In-process command/result queues owned by the independent transport task."""
    independent_heartbeat = True

    def __init__(self, session):
        self.session = session
        self.closed = False
        self.incoming = Queue(maxsize=64)

    def publish(self, packet, source):
        try:
            self.incoming.put_nowait((packet,source))
        except Full:
            self.incoming.get_nowait()
            self.incoming.put_nowait((packet,source))

    def dispatch(self, packet):
        command = json.loads(packet)
        command.pop('seq',None)
        command.pop('session_id',None)
        future = Future()
        s = self.session
        with s.lock:
            if self.closed or s.done.is_set():
                raise OSError('CONTROL_CONNECTION_CLOSED')
            radar = s.radar_status()
            if command['type'] != 'stop' and radar and radar['blocked']:
                raise OSError('RADAR_BLOCKED')
            try:
                s.requests.put_nowait((command,future,s.recovery_generation,self))
            except Full as exc:
                raise OSError('CONTROL_QUEUE_FULL') from exc
        deadline = time.monotonic()+.25
        while not future.done():
            if s.thread is None:
                s.tick()
            if s.done.wait(.002) or time.monotonic() >= deadline:
                future.cancel()
                s.stop('CONTROL_DISPATCH_TIMEOUT')
                raise OSError('CONTROL_DISPATCH_TIMEOUT')
        return future.result()

    def sendto(self, packet, target):
        if target != (self.session.host,8888):
            raise OSError('CONTROL_TARGET_MISMATCH')
        self.dispatch(packet)
        return len(packet)

    def sent_records(self):
        with self.session.lock:
            return {p['seq']:(p['type'],p['host_sent_at']) for p in self.session.tx_history}

    def recvfrom(self, size):
        if self.session.thread is None:
            self.session.tick()
        try:
            return self.incoming.get(timeout=.02)
        except Empty as exc:
            if self.closed or self.session.done.is_set():
                raise OSError('CONTROL_CONNECTION_CLOSED') from exc
            raise TimeoutError() from exc

    def close(self):
        s = self.session
        with s.lock:
            if self.closed:
                return
            self.closed = True
            s.distance_leased = False
            s.armed = s.active = False
            s.command = MotionCommand()
            s.distance_channel = None
            s.active_motion_id = None
            s.distance_finished.set()
