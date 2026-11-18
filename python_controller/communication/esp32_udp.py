"""Audited typed UDP protocol. No socket is opened by importing this module."""
import ipaddress
import json
import math
import secrets
import socket
import threading
import time
from collections import deque
from python_controller.diagnostics import safe_payload
from python_controller.communication.session_sync import SessionSync, session_fields, stopped_report
from python_controller.communication.timing import command_timing_error

from python_controller.models import MotionCommand


def number(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def uint(v):
    return type(v) is int and 0 <= v <= 0xffffffff


class Protocol:
    def __init__(self, session_id=None):
        self.session_id = session_id if session_id is not None else secrets.randbelow(0xfffffffe)+1
        if not uint(self.session_id) or self.session_id == 0:
            raise ValueError('session_id must be a nonzero uint32')
        self.seq = 0

    def packet(self, kind, **fields):
        if self.seq >= 0xfffffffe:
            raise ValueError('Sequence exhausted; stop and restart with a new session')
        self.seq += 1
        return json.dumps(dict(type=kind,session_id=self.session_id,seq=self.seq,**fields),
                          separators=(',',':'),allow_nan=False).encode('ascii')

    def stop(self):
        return self.packet('stop')

    def heartbeat(self):
        return self.packet('heartbeat')

    def reset_fault(self):
        return self.packet('reset_fault')

    def move(self, motion_id, vx, vy, target_distance_cm):
        if not uint(motion_id) or motion_id >= 2**31:
            raise ValueError('motion_id must be a nonnegative int32')
        if (type(vx) not in (int, float) or type(vy) not in (int, float) or
                (vx, vy) not in ((5, 0), (-5, 0), (0, 5), (0, -5))):
            raise ValueError('cmd_move requires a single body axis at 5 cm/s')
        if type(target_distance_cm) not in (int, float) or not 5 <= target_distance_cm <= 50:
            raise ValueError('Invalid target distance')
        return self.packet('cmd_move', motion_id=motion_id, status='SLOW',
                           vx=vx, vy=vy, w=0.0, target_distance_cm=target_distance_cm)

    def velocity(self, command):
        vx, vy, w = command.vx_cm_s, command.vy_cm_s, command.w_rad_s
        if not all(number(v) for v in (vx,vy,w)):
            raise ValueError('Invalid velocity')
        scale = max(1,math.hypot(vx,vy)/5)
        return self.packet('cmd_vel',status='SLOW',vx=vx/scale,vy=vy/scale,w=max(-.5,min(.5,w)))


class TelemetryGuard:
    def __init__(self, session_id, host, timeout_s):
        self.session_id, self.host, self.timeout = session_id, host, timeout_s
        self.data = None
        self.received_at = -math.inf
        self.boot_id = None
        self.reason = 'NO_TELEMETRY'
        self.stop_barrier = 1
        self.detail='NO_TELEMETRY'
        self.observed=None
        self.allowed_states=('STOPPED','VELOCITY','FAULT','GOAL_REACHED')

    def receive(self, packet, source, now, sent_seq):
        if source != (self.host,8888):
            return False
        try:
            if len(packet)>2048:
                raise ValueError('PACKET_TOO_LARGE')
            d = json.loads(packet)
            self.observed=safe_payload(d)
            if not isinstance(d,dict) or d.get('type') != 'telemetry':
                raise ValueError('INVALID_TELEMETRY_TYPE')
            for k in ('boot_id','session_id','last_seq','uptime_ms','command_age_ms'):
                if not uint(d.get(k)):
                    raise ValueError('INVALID_TELEMETRY_FIELDS')
            if d['boot_id']==0:raise ValueError('INVALID_BOOT_ID')
            if d['session_id']!=self.session_id:raise ValueError('SESSION_MISMATCH')
            if d['last_seq']<self.stop_barrier:raise ValueError('SEQUENCE_TOO_OLD')
            if d['last_seq']>sent_seq:raise ValueError('SEQUENCE_NOT_SENT')
            if self.boot_id is not None and d['boot_id'] != self.boot_id:
                self.boot_id, self.data = d['boot_id'], None
                raise ValueError('ESP32_REBOOT')
            if self.data and (d['uptime_ms'] <= self.data['uptime_ms'] or d['last_seq'] < self.data['last_seq']):
                self.detail='REPLAYED_OR_OUT_OF_ORDER'
                return False  # Replayed/out-of-order packets cannot extend freshness.
            if d.get('mode')!='NETWORK':raise ValueError('MODE_NOT_NETWORK')
            if d.get('state') not in self.allowed_states or not isinstance(d.get('fault'),str):
                raise ValueError('INVALID_STATE_OR_FAULT')
            if not all(number(d.get(k)) for k in ('cmd_vx','cmd_vy','cmd_w')):
                raise ValueError('command')
            for k in ('wheel_pwm','wheel_speed'):
                if not isinstance(d.get(k),list) or len(d[k]) != 3 or not all(number(x) for x in d[k]):
                    raise ValueError(k)
            for k in ('wheel_target','encoder_count'):
                if k in d and (not isinstance(d[k],list) or len(d[k])!=3 or
                               not all(number(x) and (k!='encoder_count' or type(x) is int) for x in d[k])):
                    raise ValueError(k)
            timing_error = command_timing_error(d)
            self.boot_id, self.data, self.received_at = d['boot_id'], safe_payload(d), now
            self.detail = 'ESP32_FAULT_'+d['fault'] if d['fault']!='NONE' else timing_error or 'OK'
            self.reason = self.detail
            return self.reason == 'OK'
        except (ValueError, TypeError, UnicodeError, KeyError) as exc:
            known={'PACKET_TOO_LARGE','INVALID_TELEMETRY_TYPE','INVALID_TELEMETRY_FIELDS','INVALID_BOOT_ID','SESSION_MISMATCH','SEQUENCE_TOO_OLD','SEQUENCE_NOT_SENT','ESP32_REBOOT','MODE_NOT_NETWORK','INVALID_STATE_OR_FAULT','FIRMWARE_TIMING_MISMATCH','UNSUPPORTED_PROTOCOL_VERSION'}
            self.detail=str(exc) if str(exc) in known else ('MALFORMED_JSON' if isinstance(exc,(json.JSONDecodeError,UnicodeError)) else 'INVALID_TELEMETRY_FIELDS')
            self.reason = 'INVALID_TELEMETRY'
            self.received_at = -math.inf
            return False

    def healthy(self, now):
        return self.data is not None and self.reason == 'OK' and 0 <= now-self.received_at <= self.timeout

    def link_fresh(self, now):
        return self.data is not None and 0 <= now-self.received_at <= self.timeout

    def output_stopped(self, now):
        d = self.data
        return bool(self.link_fresh(now) and d['state'] in ('STOPPED','FAULT') and
                    all(d[k] == 0 for k in ('cmd_vx','cmd_vy','cmd_w')) and
                    all(v == 0 for v in d['wheel_pwm']) and all(abs(v)<.3 for v in d['wheel_speed']))

    def ready(self, now):
        d = self.data
        return bool(self.healthy(now) and d['state'] == 'STOPPED' and
                    all(d[k] == 0 for k in ('cmd_vx','cmd_vy','cmd_w')) and
                    all(v == 0 for v in d['wheel_pwm']) and all(abs(v)<.3 for v in d['wheel_speed']))


class UdpClient:
    """One independent thread owns TX/RX; main only grants short perception leases.

    No heartbeat is sent: safe states repeatedly send STOP. The firmware watchdog
    remains a separate last line of defense if the Python process stops entirely.
    """
    requires_verified_signs = True
    allow_all_axes = False

    def __init__(self, config, *, clock=time.monotonic, socket_factory=None):
        self.cfg, self.clock, self.socket_factory = config, clock, socket_factory or socket.socket
        c = config['communication']
        self.host = str(ipaddress.IPv4Address(c['host']))
        self.protocol = Protocol()
        self.guard = TelemetryGuard(self.protocol.session_id,self.host,config['safety']['telemetry_timeout_s'])
        self.lock, self.done = threading.RLock(), threading.Event()
        self.thread = self.tx = self.rx = None
        self.armed = False
        self.failed = False
        self.guard_error=None
        self.active = False
        self.frame_timestamp = -math.inf
        self.recognized_at = None
        self.command = MotionCommand()
        self.reason = 'STARTUP_STOP'
        self.last_tx = self.last_tx_at = self.last_tx_error = None
        self.last_tx_error_details=None
        self.last_tx_interval_s=None
        self.last_tx_frame_at = None
        self.tx_history = deque(maxlen=128)
        self.stop_seq = None
        self.stop_requested_at = self.clock()
        self.rx_bound=False
        self.startup_phase='NOT_STARTED'
        self.rx_packets=0
        self.last_rx_source=self.last_rx_json=self.last_rx_any_at=None
        self.last_rx_trusted=False
        self.rx_rejections={}
        self.transport_events=deque(maxlen=256)
        self.owner = None
        self.session_sync = None
        self.session_lost = False
        self.session_diagnostic = None
        self.sync_deadline = None

    def configure_owner(self, owner, previous=None):
        self.owner = owner
        self.session_sync = SessionSync(previous)

    def start(self):
        if self.socket_factory is socket.socket and not getattr(self, 'central_session', False):
            raise ValueError('RADAR_DASHBOARD_REQUIRED')
        if self.requires_verified_signs and self.cfg['communication']['signs_verified'] is not True:
            raise ValueError('Physical coordinate signs must be verified in config before HARDWARE')
        try:
            self.startup_phase='TX_SOCKET'
            self.tx = self.socket_factory(socket.AF_INET,socket.SOCK_DGRAM)
            self.rx = self.socket_factory(socket.AF_INET,socket.SOCK_DGRAM)
            self.startup_phase='RX_BIND'
            if (self.owner and self.socket_factory is socket.socket and
                    hasattr(socket, "SO_EXCLUSIVEADDRUSE")):
                self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                self.tx.settimeout(.02)
            self.rx.bind(('',8889))
            self.rx_bound=True
            self.rx.setblocking(False)
            self.sync_deadline = self.clock() + 2
            self.thread = threading.Thread(target=self._loop,name='ESP32-safety-guard',daemon=True)
            self.thread.start()
            self.startup_phase='RUNNING'
        except Exception:
            self.close()
            raise

    def arm(self):
        with self.lock:
            stopped_after_request=(self.stop_seq is not None and self.guard.data is not None and
                                   self.guard.data['last_seq']>=self.stop_seq and
                                   self.guard.received_at>=self.stop_requested_at)
            if not self.failed and not self.session_lost and self.guard.ready(self.clock()) and stopped_after_request:
                self.armed, self.reason = True, 'ARMED'
                self.active = False
                self.frame_timestamp = self.clock()
                return True
            return False

    def submit(self, decision, frame_timestamp, recognized_at=None):
        with self.lock:
            self.command, self.frame_timestamp = decision.command, frame_timestamp
            self.recognized_at = recognized_at
            self.active = decision.state in ('FOLLOWING','GESTURE_OVERRIDE')

    def stop(self, reason='USER_STOP'):
        with self.lock:
            self._latch(reason)

    def _latch(self, reason):
        if not self.armed and not self.active and self.reason == reason and self.stop_seq is not None:
            return
        self.armed = self.active = False
        self.command, self.reason = MotionCommand(), reason
        self.stop_seq = None
        self.stop_requested_at = self.clock()

    def snapshot(self):
        with self.lock:
            now = self.clock()
            data = self.guard.data
            acked = next((p for p in reversed(self.tx_history) if data and p['seq']==data['last_seq']),None)
            return dict(connected=not self.failed and not self.session_lost and self.guard.healthy(now),ready=not self.failed and not self.session_lost and self.guard.ready(now),armed=self.armed,
                        fault=self.guard.data['fault'] if self.guard.data else 'UNKNOWN',
                        reason=self.reason,telemetry=data,telemetry_valid=self.guard.healthy(now) and not self.failed,
                        telemetry_reason=self.guard.reason,last_rx_at=self.guard.received_at if math.isfinite(self.guard.received_at) else None,
                        telemetry_age_s=now-self.guard.received_at if math.isfinite(self.guard.received_at) else None,
                        last_tx=self.last_tx,last_tx_at=self.last_tx_at,last_tx_frame_at=self.last_tx_frame_at,tx_error=self.last_tx_error,
                        tx_error_details=self.last_tx_error_details,
                        last_tx_interval_s=self.last_tx_interval_s,
                        guard_error=self.guard_error,
                        acknowledged_command=acked,processed_command=acked,ack_protocol='NO_SEPARATE_ACK_PACKET',session_id=self.protocol.session_id,
                        rx_bound=self.rx_bound,rx_packets=self.rx_packets,last_rx_source=self.last_rx_source,
                        last_rx_json=self.last_rx_json,last_rx_any_at=self.last_rx_any_at,last_rx_trusted=self.last_rx_trusted,
                        validation_detail=self.guard.detail,rx_rejections=dict(self.rx_rejections),
                        session_lost=self.session_lost,session_diagnostic=self.session_diagnostic,
                        tx_destination=[self.host,8888],heartbeat_verified=bool(acked and acked['type']=='heartbeat' and self.guard.healthy(now)),
                        stop_requested_at=self.stop_requested_at,stop_seq=self.stop_seq,
                        link_alive=self.guard.link_fresh(now),
                        stop_verified=bool(not self.active and self.guard.output_stopped(now) and self.stop_seq is not None and
                                           data['last_seq']>=self.stop_seq and self.guard.received_at>=self.stop_requested_at))

    def tick(self):
        """One deterministic iteration, also exercised with fake sockets/time."""
        with self.lock:
            now = self.clock()
            for _ in range(32):
                try:
                    packet, source = self.rx.recvfrom(2049)
                except BlockingIOError:
                    break
                self.rx_packets+=1
                self.last_rx_any_at=now;self.last_rx_source=list(source)
                try:self.last_rx_json=safe_payload(json.loads(packet))
                except (ValueError,UnicodeError):self.last_rx_json=None
                self.last_rx_trusted=source==(self.host,8888)
                if source != (self.host,8888):
                    self.rx_rejections['SOURCE_MISMATCH']=self.rx_rejections.get('SOURCE_MISMATCH',0)+1
                    self.transport_events.append(dict(event='UDP_RX',source=list(source),received_at=now,accepted=False,reason='SOURCE_MISMATCH',json=self.last_rx_json,payload_filtered=True))
                    continue
                if not self._accept_transport_packet(self.last_rx_json, now):
                    continue
                previous_boot = self.guard.boot_id
                previous_data = self.guard.data
                accepted=self.guard.receive(packet,source,now,self.protocol.seq)
                if self.session_sync and self.guard.observed:
                    report = self.guard.observed
                    reason = None
                    if self.guard.detail == "SESSION_MISMATCH":
                        reason = self.session_sync.foreign(report, stopped_report(report))
                        if (reason == "PREVIOUS_SESSION_DURING_STOP" and
                                self.sync_deadline is not None and now >= self.sync_deadline):
                            reason = "SESSION_MISMATCH"
                    elif self.guard.detail == "ESP32_REBOOT" or (
                            accepted and not self.session_sync.boot_matches(report)):
                        reason = "ESP32_REBOOT"
                    if reason:
                        fields = self._audit(report, reason)
                        self.session_diagnostic = fields
                        self.transport_events.append(dict(event="SESSION_DIAGNOSTIC", **fields))
                        if reason == "PREVIOUS_SESSION_DURING_STOP":
                            continue
                        self.session_lost = True
                        self._latch(reason)
                    elif accepted:
                        self.session_sync.proven = True
                if not accepted:self.rx_rejections[self.guard.detail]=self.rx_rejections.get(self.guard.detail,0)+1
                stop_verified = bool(self.stop_seq is not None and self.guard.output_stopped(now)
                                     and self.guard.data["last_seq"] >= self.stop_seq
                                     and self.guard.received_at >= self.stop_requested_at)
                self.transport_events.append(dict(event='UDP_RX',source=list(source),received_at=now,accepted=accepted,reason=self.guard.detail,json=self.last_rx_json,payload_filtered=True,
                                                  **self._audit(self.last_rx_json, verified=stop_verified,
                                                                stop_sent=self.stop_seq is not None)))
                if self.guard.data is not None and self.guard.data is not previous_data:
                    self._on_telemetry(self.guard.data,now)
                if previous_boot is not None and self.guard.boot_id != previous_boot:
                    self._latch('ESP32_REBOOT')
                    self.guard.stop_barrier = self.protocol.seq+1
                if self.guard.reason != 'OK':
                    self._latch(self.guard.reason)
                self._on_transport_packet(packet, source, now, accepted)
            packet,moving = self._select_packet(now)
            if packet is not None:
                self._send_packet(packet,moving)

    def _accept_transport_packet(self, data, now):
        return True

    def _on_transport_packet(self, packet, source, now, accepted):
        pass

    def _audit(self, telemetry=None, reason=None, *, verified=False, stop_sent=False):
        return (session_fields(self.owner, self.protocol.session_id, self.protocol.seq, telemetry, reason,
                               stop_sent=stop_sent, stop_verified=verified) if self.owner else {})

    def _on_telemetry(self,data,now):
        pass

    def _select_packet(self,now):
        if self.armed and not self.guard.healthy(now):
            self._latch('TELEMETRY_STALE_OR_INVALID')
        if self.armed and not 0 <= now-self.frame_timestamp <= self.cfg['safety']['perception_timeout_s']:
            self._latch('PERCEPTION_LEASE_EXPIRED')
        cmd = self.command
        if self.armed and self.active:
            # Sign follows validated config; relative reverse and lateral motion blocked.
            if (not all(number(v) for v in (cmd.vx_cm_s,cmd.vy_cm_s,cmd.w_rad_s)) or
                (not self.allow_all_axes and (cmd.vx_cm_s*self.cfg['follow']['forward_sign'] < 0 or cmd.vy_cm_s != 0))):
                self._latch('INVALID_OR_REVERSE_MOTION')
        moving = self.armed and self.active
        packet = self.protocol.velocity(cmd) if moving else self.protocol.stop()
        return packet,moving

    def _send_packet(self,packet,moving=False):
        try:
            sent = self.tx.sendto(packet,(self.host,8888))
            if sent is not None and sent != len(packet): raise OSError('Partial UDP send')
        except OSError as exc:
            self.last_tx_error = type(exc).__name__
            self.last_tx_error_details=dict(error_type=type(exc).__name__,error_code=getattr(exc,'winerror',None) or exc.errno)
            self.transport_events.append(dict(event='UDP_TX',destination=[self.host,8888],sent_at=self.clock(),
                command=json.loads(packet),socket_send_completed=False,**self.last_tx_error_details))
            raise
        now=self.clock()
        recognized = self.recognized_at if moving and self.recognized_at is not None and self.recognized_at <= now else None
        self.last_tx_interval_s=now-self.last_tx_at if self.last_tx_at is not None else None
        self.last_tx,self.last_tx_at,self.last_tx_error = json.loads(packet),now,None
        self.last_tx_error_details=None
        self.last_tx_frame_at=self.frame_timestamp if moving and math.isfinite(self.frame_timestamp) else None
        latency = dict(perception_frame_at=self.last_tx_frame_at, recognized_at=recognized,
                       recognition_to_tx_ms=(now-recognized)*1000 if recognized is not None else None)
        self.tx_history.append({**self.last_tx,'host_sent_at':self.last_tx_at,**latency})
        if self.last_tx['type']=='stop' and self.stop_seq is None:
            self.stop_seq = self.last_tx['seq']
        self._after_send(self.last_tx)
        self.transport_events.append(dict(event='UDP_TX',destination=[self.host,8888],sent_at=self.last_tx_at,interval_s=self.last_tx_interval_s,command=dict(self.last_tx),socket_send_completed=True,
                                          **latency, **self._audit(stop_sent=self.last_tx['type']=='stop')))

    def _after_send(self,packet):
        pass

    def drain_transport_events(self):
        with self.lock:
            events=list(self.transport_events);self.transport_events.clear();return events

    def _loop(self):
        try:
            while not self.done.is_set():
                self.tick()
                self.done.wait(self.cfg['communication']['send_period_s'])
        except Exception as exc:
            with self.lock:
                self.failed = True
                self.guard_error=dict(error_type=type(exc).__name__,error_code=getattr(exc,'winerror',None) or getattr(exc,'errno',None))
                self.transport_events.append(dict(event='UDP_GUARD_FAILED',**self.guard_error))
                self._latch('UDP_GUARD_FAILED')
                if self.tx is not None:
                    try:
                        self._send_packet(self.protocol.stop())
                    except (OSError,ValueError):
                        pass

    def stop_and_release(self, timeout=.85):
        self.stop("CONTROL_RELEASE")
        deadline = time.monotonic() + timeout
        evidence = self.snapshot()
        while self.thread is not None and self.thread.is_alive() and not evidence["stop_verified"] and time.monotonic() < deadline:
            time.sleep(.02)
            evidence = self.snapshot()
        self.close(final_stop=not evidence["stop_verified"])
        return dict(host=self.host, socket_closed=not self.rx_bound and not bool(self.thread and self.thread.is_alive()),
                    **self._audit(evidence["telemetry"], verified=evidence["stop_verified"],
                                  stop_sent=bool(evidence["stop_seq"] or self.stop_seq)))

    def close(self, *, final_stop=True):
        self.done.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
        with self.lock:
            self._latch('CLOSED')
            if self.tx and final_stop:
                try:
                    self._send_packet(self.protocol.stop())
                except (OSError,ValueError):
                    pass
            for sock in (self.rx,self.tx):
                if sock:
                    sock.close()
            self.rx = self.tx = None
            self.rx_bound=False
