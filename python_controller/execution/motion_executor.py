"""A one-shot, synchronous distance controller; no camera or model calls during motion."""
import ipaddress
import json
import math
from secrets import randbelow
import time

from python_controller.communication.esp32_udp import Protocol
from python_controller.communication.session_sync import session_fields
from python_controller.execution.control_lease import ESP32_COMMAND_PORT, open_control_socket
from python_controller.execution.llm_telemetry import (
    COMMAND_FIELDS, GOAL_STATES, GoalTelemetryGuard, TelemetryError, stationary,
)
from python_controller.planning.schema import Move, Stop
from python_controller.planning.validator import ValidatedPlan


VELOCITIES = {"forward": (5, 0), "backward": (-5, 0), "left": (0, 5), "right": (0, -5)}
HEARTBEAT_S = 0.10
WATCHDOG_S = 0.30
STOP_TIMEOUT_S = 2.0


class MotionError(RuntimeError):
    pass


class LLMMotionExecutor:
    def __init__(self, host, emit, *, timeout=.8, clock=time.monotonic, socket_factory=open_control_socket, previous=None, protocol=None):
        address = ipaddress.IPv4Address(host)
        if address.is_unspecified or address.is_multicast or address == ipaddress.IPv4Address("255.255.255.255"):
            raise MotionError("INVALID_ESP32_IP")
        if not .4 <= timeout <= .8:
            raise MotionError("INVALID_TELEMETRY_TIMEOUT")
        self.target = (str(address), ESP32_COMMAND_PORT)
        self.emit, self.clock, self.socket_factory = emit, clock, socket_factory
        self.timeout = timeout
        self.protocol = protocol if protocol is not None else Protocol()
        self.sent = {}
        self.guard = GoalTelemetryGuard(self.target, self.protocol.session_id, timeout, previous=previous)
        self.sock = None
        self.release = None
        self.independent_transport = False
        self.last_sent_seq = 0
        self.last_sent_command = None
        self.owned_session = None

    def _open_transport(self):
        if self.socket_factory is not open_control_socket:
            return self.socket_factory()
        from python_controller.communication.robot_session import RobotSession
        from python_controller.config import load_config
        config = load_config()
        config['communication']['host'] = self.target[0]
        session = self.owned_session = RobotSession(config,clock=self.clock)
        session.configure_owner('LLM_COMMAND',self.guard.session_sync.previous)
        try:
            session.start()
            deadline = self.clock()+STOP_TIMEOUT_S
            while self.clock()<deadline:
                self._check_stop()
                state = session.snapshot()
                if state['ready'] and state['stop_verified']:
                    self.protocol = Protocol(session.protocol.session_id)
                    self.guard = GoalTelemetryGuard(self.target,self.protocol.session_id,self.timeout,
                        previous=dict(tx_session_id=session.protocol.session_id,tx_seq=session.protocol.seq,
                                      boot_id=state['telemetry']['boot_id']))
                    self.sent.update({p['seq']:(p['type'],p['host_sent_at']) for p in session.tx_history})
                    return session.distance_socket()
                time.sleep(.01)
            raise MotionError('STOP_NOT_CONFIRMED')
        except BaseException:
            session.close()
            raise

    def run(self, plan, *, approved=False, stop_requested=None):
        if approved is not True:
            raise MotionError("OPERATOR_APPROVAL_REQUIRED")
        if not isinstance(plan, ValidatedPlan) or plan.needs_clarification:
            raise MotionError("VALIDATED_PLAN_REQUIRED")
        if self.sock is not None:
            raise MotionError("EXECUTOR_ALREADY_USED")
        self.stop_requested = stop_requested
        self._check_stop()
        try:
            self.sock = self._open_transport()
            self.independent_transport = getattr(self.sock,'independent_heartbeat',False) is True
        except OSError:
            raise MotionError("UDP_BIND_FAILED") from None
        complete = False
        try:
            self.emit("HANDSHAKE", session_id=self.protocol.session_id, **self._session())
            stopped = self._confirm_stop()
            retained_id = stopped.get("motion_id")
            max_start_id = 2**31 - len(plan.actions)
            first_id = randbelow(max_start_id) + 1
            # Firmware may keep the last goal's content after STOP and across sessions.
            if retained_id is not None and first_id <= retained_id < first_id + len(plan.actions):
                first_id = 1 if retained_id >= max_start_id else retained_id + 1
            for motion_id, action in enumerate(plan.actions, first_id):
                if isinstance(action, Stop):
                    # Each move ends with STOP, as does a standalone STOP handshake.
                    break
                self._confirm_heartbeat()
                self._move(motion_id, action)
                self._confirm_stop()
            self._check_stop()
            self.emit("COMPLETED")
            complete = True
        except KeyboardInterrupt:
            raise MotionError("OPERATOR_STOP") from None
        except TelemetryError as exc:
            raise MotionError(str(exc)) from None
        except OSError:
            raise MotionError("UDP_IO_FAILED") from None
        except MotionError:
            raise
        except Exception:
            raise MotionError("EXECUTOR_FAILURE") from None
        finally:
            try:
                if not complete:
                    self._abort_stop()
            finally:
                self.sock.close()
                if self.owned_session is not None:
                    self.release = self.owned_session.stop_and_release()
                if complete:
                    if self.owned_session is None:
                        self.release = self._session(self.guard.observed, verified=True, stop_sent=True)

    def _session(self, data=None, reason=None, *, verified=False, stop_sent=False):
        seq = self.sock.session.protocol.seq if self.independent_transport else self.protocol.seq
        return session_fields("LLM_COMMAND", self.protocol.session_id, seq,
                              data, reason, stop_sent=stop_sent, stop_verified=verified)

    def _check_stop(self):
        if self.owned_session is not None:
            self.owned_session.controller_alive()
        if self.independent_transport:
            self.sock.session.execution_alive()
        if self.stop_requested is not None and self.stop_requested():
            raise MotionError("OPERATOR_STOP")

    def _send(self, packet, *, retry=False):
        self._check_stop()
        data = json.loads(packet)
        now = self.clock()
        if self.independent_transport:
            data, now = self.sock.dispatch(packet)
        elif self.sock.sendto(packet, self.target) != len(packet):
            raise OSError("Partial UDP send")
        self.last_sent_seq = data['seq']
        self.last_sent_command = data
        self.sent.setdefault(data["seq"], (data["type"], now))
        self.emit("UDP_TX", command=data, sent_monotonic_s=now, retry=retry,
                  **self._session(stop_sent=data["type"] == "stop"))
        return data["seq"], now

    def _receive(self):
        self._check_stop()
        try:
            raw, source = self.sock.recvfrom(2049)
        except TimeoutError:
            return None
        self._check_stop()
        now = self.clock()
        if self.independent_transport:
            self.sent.update(self.sock.sent_records())
        try:
            data = self.guard.receive(raw, source, now, self.sent)
        except TelemetryError as exc:
            self.emit("SESSION_DIAGNOSTIC", telemetry=self.guard.observed,
                      **self._session(self.guard.observed, str(exc)))
            raise
        if self.guard.rejection:
            self.emit("SESSION_DIAGNOSTIC", **self._session(self.guard.observed, self.guard.rejection))
        if data:
            kind, sent_at = self.sent[data["last_seq"]]
            self.emit("TELEMETRY", telemetry=data, received_monotonic_s=now,
                      echoed_kind=kind, echoed_packet_age_s=now - sent_at, **self._session(data))
        return data

    def _confirm_stop(self):
        barrier, sent_at = self._send(self.protocol.stop())
        deadline = sent_at + STOP_TIMEOUT_S
        while self.clock() < deadline:
            if self.clock() - sent_at >= HEARTBEAT_S:
                _, sent_at = self._send(self.protocol.stop())
            data = self._receive()
            if (data and self.clock() < deadline and self.guard.fresh(self.clock())
                    and data["last_seq"] >= barrier and data["state"] == "STOPPED" and stationary(data)):
                self.emit("STOP_CONFIRMED", telemetry=data, stop_barrier=barrier,
                          **self._session(data, verified=True, stop_sent=True))
                return data
        raise MotionError("STOP_NOT_CONFIRMED")

    def _confirm_heartbeat(self):
        barrier, sent_at = self._send(self.protocol.heartbeat())
        deadline = sent_at + self.timeout
        while self.clock() < deadline:
            if not self.guard.fresh(self.clock()):
                raise MotionError("TELEMETRY_STALE")
            if self.clock() - sent_at >= HEARTBEAT_S:
                _, sent_at = self._send(self.protocol.heartbeat())
            data = self._receive()
            if data and (data["state"] != "STOPPED" or not stationary(data)):
                raise MotionError("UNEXPECTED_MOTION_BEFORE_DISPATCH")
            if (data and data["last_seq"] >= barrier and self.clock() < deadline
                    and self.guard.fresh(self.clock())):
                self.emit("HEARTBEAT_CONFIRMED", telemetry=data)
                return
        raise MotionError("HEARTBEAT_NOT_CONFIRMED")

    def _move(self, motion_id: int, action: Move):
        if not self.guard.fresh(self.clock()):
            raise MotionError("TELEMETRY_STALE")
        distance = float(action.distance_cm)
        packet = self.protocol.move(motion_id, *VELOCITIES[action.direction], distance)
        barrier, sent_at = self._send(packet)
        expected_token = self.last_sent_command.get('execution_token')
        acceptance_deadline = sent_at + self.timeout
        goal_deadline = sent_at + distance / 5 + 7
        liveness_at = sent_at
        retried = accepted = False
        while True:
            now = self.clock()
            gap = now - liveness_at
            if not self.independent_transport and gap >= WATCHDOG_S:
                self.emit("HEARTBEAT_LATE", interval_s=gap)
                raise MotionError("HEARTBEAT_DEADLINE")
            if not self.guard.fresh(now):
                raise MotionError("TELEMETRY_STALE")
            if not accepted and now >= acceptance_deadline:
                raise MotionError("MOVE_NOT_CONFIRMED")
            if now >= goal_deadline:
                raise MotionError("GOAL_TIMEOUT")
            if not self.independent_transport and gap >= HEARTBEAT_S:
                previous_liveness = liveness_at
                _, liveness_at = self._send(self.protocol.heartbeat())
                self.emit("HEARTBEAT_INTERVAL", interval_s=liveness_at - previous_liveness)
            elif not retried and not accepted and now - sent_at >= .05:
                # A heartbeat with a newer seq would make this exact retry stale.
                if self.independent_transport or self.protocol.seq == barrier:
                    self._send(packet, retry=True)
                retried = True
            data = self._receive()
            now = self.clock()
            if (not data or not self.guard.fresh(now) or data["last_seq"] < barrier
                    or (not self.independent_transport and now - liveness_at >= WATCHDOG_S) or now >= goal_deadline
                    or (not accepted and now >= acceptance_deadline)):
                continue
            if data["state"] not in GOAL_STATES:
                if accepted:
                    raise MotionError("GOAL_INTERRUPTED")
                continue
            if expected_token is not None and data.get('execution_token') != expected_token:
                raise MotionError('EXECUTION_TOKEN_MISMATCH')
            if (data["motion_id"] != motion_id or
                    not math.isclose(data["goal_target_cm"], distance, abs_tol=1e-4)):
                raise MotionError("MOTION_ID_MISMATCH")
            if not accepted:
                accepted = True
                self.emit("MOVE_ACCEPTED", telemetry=data, move_seq=barrier)
            if data["state"] == "GOAL_REACHED":
                remaining = data["remaining_cm"]
                if (not 0 <= remaining <= .5001 or abs(data["lateral_error_cm"]) > 2.0001
                        or not math.isclose(remaining, data["goal_target_cm"] - data["goal_progress_cm"],
                                            rel_tol=0, abs_tol=.001)
                        or data["overshoot_cm"] != 0 or any(data[key] != 0 for key in COMMAND_FIELDS)
                        or any(pwm != 0 for pwm in data["wheel_pwm"])
                        or any(abs(speed) > .8001 for speed in data["wheel_speed"])):
                    raise MotionError("INCONSISTENT_GOAL_COMPLETION")
                self.emit("GOAL_REACHED", telemetry=data)
                return

    def _abort_stop(self):
        # Report delivery as unconfirmed. Logging failures must not block STOP.
        attempts = []
        barrier = None
        for _ in range(3):
            seq = self.protocol.seq
            try:
                packet = self.protocol.stop()
                if self.independent_transport:
                    data, sent_at = self.sock.dispatch(packet)
                    seq = data['seq']
                    sent = True
                else:
                    seq = self.protocol.seq
                    sent_at = self.clock()
                    sent = self.sock.sendto(packet, self.target) == len(packet)
                if sent:
                    self.sent[seq] = ("stop", sent_at)
                    if barrier is None:
                        barrier = seq
            except (OSError, ValueError):
                sent = False
            attempts.append((seq, sent))
        for seq, sent in attempts:
            try:
                fields = self._session(stop_sent=sent)
                fields["tx_seq"] = seq
                self.emit("ABORT_STOP_ATTEMPT", session_id=self.protocol.session_id, seq=seq,
                          socket_send_completed=sent, stop_confirmed=False, **fields)
            except OSError:
                pass
        data = None
        deadline = self.clock() + min(.6, self.timeout)
        # A stop request is sticky; this bounded read cannot send a motion packet.
        while barrier is not None and self.clock() < deadline:
            try:
                payload, source = self.sock.recvfrom(2049)
                if self.independent_transport:
                    self.sent.update(self.sock.sent_records())
                sample = self.guard.receive(payload, source, self.clock(), self.sent, abort_stop=True)
            except (TimeoutError, TelemetryError):
                continue
            except (OSError, KeyboardInterrupt):
                break
            if (sample and sample["last_seq"] >= barrier and self.clock() < deadline and
                sample["state"] in ("STOPPED", "FAULT") and stationary(sample)):
                data = sample
                break
        self.release = self._session(data, verified=data is not None, stop_sent=barrier is not None)
        try:
            self.emit("ABORT_STOP_RESULT", telemetry=data, **self.release)
        except OSError:
            pass
