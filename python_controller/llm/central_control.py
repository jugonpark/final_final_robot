"""Run dashboard planning and distance execution outside the UI thread."""
from ipaddress import IPv4Address
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread
from time import monotonic
from uuid import uuid4
from python_controller.communication.timing import CONTROLLER_LEASE_S
from python_controller.execution.history_store import MissionHistory


class ControlError(ValueError):
    pass


class ExecutionProgress:
    """Display state consumed from the executor queue by the control thread."""
    def __init__(self, plan, plan_ref):
        from python_controller.planning.schema import Move, describe_action

        self.plan_id, self.revision = plan_ref
        self.run_id = uuid4().hex
        self.labels = tuple(describe_action(action) for action in plan.actions if isinstance(action, Move))
        self.steps = ['WAITING'] * len(self.labels)
        self.state = 'EXECUTING'
        self.verified = 0
        self.current = None
        self.move_id = self.move_seq = self.last_stop_barrier = None
        self.completed = False
        self.error = None
        self.stop_reason = None
        self.started_at = monotonic()
        self.sequence = 1

    def ingest(self, event, fields):
        if self.state != 'EXECUTING':
            return
        telemetry = fields.get('telemetry') or {}
        if event == 'MOVE_ACCEPTED' and self.last_stop_barrier is not None and self.verified < len(self.steps):
            if (self.steps[self.verified] == 'WAITING' and fields['move_seq'] > self.last_stop_barrier
                    and telemetry['motion_id'] != self.move_id):
                self.current = self.verified
                self.move_id, self.move_seq = telemetry['motion_id'], fields['move_seq']
                self.steps[self.current] = 'MOVING'
                return event, self.current + 1
        elif event == 'GOAL_REACHED' and self.current == self.verified:
            if self.steps[self.current] == 'MOVING' and telemetry['motion_id'] == self.move_id:
                self.steps[self.current] = 'GOAL_REPORTED'
                return event, self.current + 1
        elif event == 'STOP_CONFIRMED':
            barrier = fields['stop_barrier']
            if self.last_stop_barrier is None:
                self.last_stop_barrier = barrier  # Initial STOP is the handshake, not a completed move.
                return 'HANDSHAKE_STOP_CONFIRMED', None
            elif self.current == self.verified and self.steps[self.current] == 'GOAL_REPORTED' and barrier > self.move_seq:
                self.last_stop_barrier = barrier
                self.steps[self.current] = 'STOP_VERIFIED'
                self.verified += 1
                return event, self.current + 1
        elif event == 'COMPLETED':
            self.completed = self.last_stop_barrier is not None and self.verified == len(self.steps)

    def finish(self, event, error):
        if event == 'DONE' and self.completed and self.state == 'EXECUTING':
            self.state = 'COMPLETED'
            return
        self.error = (self.stop_reason if self.stop_reason and error in (None, 'OPERATOR_STOP') else error) or 'PROGRESS_NOT_VERIFIED'
        self.state = 'ABORTED'
        self.steps[self.verified:] = ['BLOCKED'] * (len(self.steps) - self.verified)

    def snapshot(self):
        return {'plan_id': self.plan_id, 'revision': self.revision, 'run_id': self.run_id, 'state': self.state,
                'steps': self.steps.copy(), 'labels': self.labels, 'verified_count': self.verified,
                'current_step': self.current + 1 if self.current is not None else None, 'last_error': self.error}


class NaturalControl:
    def __init__(self, folder, lease_s):
        self.folder = Path(folder) / "llm"
        self.lease_s = lease_s
        self.phase = "IDLE"
        self.plan = None
        self.detail = None
        self.log_path = None
        self.last_feedback = None
        self.last_echo_age_s = None
        self.session_info = None
        self.previous = self.release = self.session_diagnostic = self.stop_result = None
        self._thread = None
        self._stop = Event()
        self._events = SimpleQueue()
        self._last_poll = monotonic()
        self.motion_session = None
        self.session_guard = None
        self.progress = None
        self.history = MissionHistory(self.folder / 'mission-history.sqlite3')

    @property
    def busy(self):
        return self._thread is not None

    @property
    def executing(self):
        return self.phase in ("EXECUTING", "STOPPING")

    def discard(self):
        if not self.busy:
            self.plan = self.detail = None
            self.phase = "IDLE"

    def start_plan(self, command):
        if not command.strip():
            raise ControlError("INPUT_LENGTH")
        self._start("PLANNING", "READY", lambda: self._prepare(command))
        self.plan = None
        self.log_path = self.last_feedback = None
        self.last_echo_age_s = None

    def start_motion(self, host, prepared, *, session=None, validated_plan=None, session_guard=None, plan_ref=None):
        if self.busy or (validated_plan is None and (self.phase != "READY" or self.plan is None)):
            raise ControlError("VALIDATED_PLAN_REQUIRED")
        if validated_plan is not None:
            from python_controller.planning.validator import ValidatedPlan
            if not isinstance(validated_plan, ValidatedPlan) or session is None or session_guard is None:
                raise ControlError('VALIDATED_PLAN_REQUIRED')
        plan = validated_plan if validated_plan is not None else self.plan
        if plan.requires_angle_control:
            raise ControlError("ROTATION_CONTROL_NOT_VERIFIED")
        if prepared is not True:
            raise ControlError("HARDWARE_PREPARATION_REQUIRED")
        try:
            address = IPv4Address(host)
            if address.is_unspecified or address.is_multicast or int(address) == 0xffffffff:
                raise ValueError
        except ValueError:
            raise ControlError("INVALID_ESP32_IP") from None
        # A ready plan is a one-use approval. The worker keeps its immutable copy.
        self.motion_session = session
        self.session_guard = session_guard
        self.plan = plan
        self.session_diagnostic = None
        self.session_info = None
        self.stop_result = None
        self.progress = ExecutionProgress(plan, plan_ref or (uuid4().hex, 1))
        self.history.start(self.progress.run_id, self.progress.plan_id, self.progress.revision,
                           [action.model_dump(mode='json') for action in plan.actions])
        try:
            self._start("EXECUTING", "DONE", lambda: self._execute(str(address), plan))
        except ControlError as exc:
            self._finish_progress('FAILED', str(exc))
            raise

    def progress_snapshot(self):
        return self.progress.snapshot() if self.progress else None

    def _record(self, event, *, step=None, source='executor', reason=None, fields=None):
        progress = self.progress
        progress.sequence += 1
        self.history.record(progress.run_id, progress.sequence, event, source=source, step=step, reason=reason,
                            fields=fields, elapsed_ms=round(1000 * (monotonic() - progress.started_at)))

    def _finish_progress(self, event, data):
        progress = self.progress
        progress.finish(event, data)
        step = progress.current + 1 if progress.current is not None and progress.steps[progress.current] != 'STOP_VERIFIED' else None
        self._record(progress.state, source='supervisor', reason=progress.error, step=step)

    def _start(self, phase, terminal, task):
        if self.busy:
            raise ControlError("COMMAND_BUSY")
        self.phase, self.detail = phase, None
        self._stop.clear()
        self._last_poll = monotonic()

        def run():
            try:
                result = task()
            except ModuleNotFoundError:
                self._events.put(("FAILED", "LLM_DEPENDENCIES_MISSING"))
            except ControlError as exc:
                self._events.put(("FAILED", str(exc)))
            except OSError:
                self._events.put(("FAILED", "LOG_IO_FAILED"))
            except Exception:
                self._events.put(("FAILED", "UNEXPECTED_FAILURE"))
            else:
                self._events.put((terminal, result))

        self._thread = Thread(target=run, name=f"natural-control-{phase.lower()}", daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            self._thread = None
            self.plan = None
            self.phase, self.detail = "FAILED", "UNEXPECTED_FAILURE"
            raise ControlError(self.detail) from None

    @staticmethod
    def _prepare(command):
        from python_controller.llm_cli import load_environment
        from python_controller.planning.dashboard_command import prepare_dashboard_plan
        from python_controller.llm.nvidia_client import NvidiaError
        from python_controller.planning.validator import PlanError

        try:
            return prepare_dashboard_plan(command, load_environment())
        except (PlanError, NvidiaError) as exc:
            raise ControlError(str(exc)) from None

    def _execute(self, host, plan):
        from python_controller.execution.motion_executor import LLMMotionExecutor, MotionError
        from python_controller.execution.run_log import RunLog
        from python_controller.llm_cli import load_environment, motion_timeout
        from python_controller.planning.validator import PlanError, POLICY_VERSION

        log = RunLog(self.folder)
        self._events.put(("LOG", log.path))

        def emit(event, **fields):
            log.emit(event, **fields)
            if event in ("SESSION_DIAGNOSTIC", "ABORT_STOP_RESULT"):
                self._events.put((event, fields))
            elif event == "TELEMETRY":
                self._events.put((event, (fields.get("telemetry"), fields.get("echoed_packet_age_s"))))
            elif event == "HANDSHAKE":
                self._events.put((event, fields))
            elif event in ("MOVE_ACCEPTED", "GOAL_REACHED", "STOP_CONFIRMED", "COMPLETED"):
                self._events.put((event, fields))

        executor = None
        try:
            timeout = motion_timeout(load_environment())
            config_actions = [action.model_dump(mode="json") for action in plan.actions]
            log.emit("APPROVED", source="DASHBOARD", host=host, policy_version=POLICY_VERSION,
                     actions=config_actions, physical_distance_cm=None)
            options = {"timeout": timeout}
            if self.motion_session is not None:
                session = self.motion_session
                snapshot = session.snapshot()
                from python_controller.communication.esp32_udp import Protocol
                options.update(clock=session.clock, socket_factory=self.session_guard or session.distance_socket,
                               protocol=Protocol(session.protocol.session_id),
                               previous=dict(tx_session_id=session.protocol.session_id,
                                             tx_seq=session.protocol.seq, **{
                                                 key: snapshot['telemetry'][key] for key in ('boot_id',)
                                             }))
            elif self.previous:
                options["previous"] = self.previous
            executor = LLMMotionExecutor(host, emit, **options)
            if self.motion_session is not None:
                with self.motion_session.lock:
                    executor.sent.update({packet['seq']: (packet['type'], packet['host_sent_at'])
                                          for packet in self.motion_session.tx_history})
            executor.run(
                plan, approved=True, stop_requested=self._stop_requested,
            )
        except (MotionError, PlanError) as exc:
            log.emit("ABORTED", reason=str(exc))
            raise ControlError(str(exc)) from None
        finally:
            if executor is not None and executor.release and self.motion_session is None:
                self._events.put(("RELEASE", dict(host=host, socket_closed=True, **executor.release)))
            log.close()

    def _stop_requested(self):
        if self._stop.is_set():
            return True
        if self.motion_session is not None:
            self.motion_session.execution_alive()
            if self.motion_session.distance_cancelled:
                self._stop.set()
                return True
        lease = CONTROLLER_LEASE_S if self.motion_session is not None else self.lease_s
        if monotonic() - self._last_poll > lease:
            self._stop.set()
            return True
        return False

    def poll(self):
        self._last_poll = monotonic()
        while True:
            try:
                event, data = self._events.get_nowait()
            except Empty:
                return
            if event == "LOG":
                self.log_path = data
            elif event == "RELEASE":
                self.release = data
            elif event == "SESSION_DIAGNOSTIC":
                self.session_diagnostic = data
            elif event == "ABORT_STOP_RESULT":
                self.stop_result = data
                if self.progress and self.executing:
                    self._record(event, fields=data)
            elif event == "TELEMETRY":
                self.last_feedback, self.last_echo_age_s = data
            elif event == "HANDSHAKE":
                self.detail, self.session_info = event, data
            elif event in ("READY", "FAILED", "DONE"):
                if self.progress and self.executing:
                    self._finish_progress(event, data)
                self._thread = None
                if self._stop.is_set() and event == "READY":
                    self.phase, self.detail = "CANCELLED", None
                    continue
                self.phase = event
                self.plan = data if event == "READY" else None
                self.detail = data if event == "FAILED" else None
            elif event in ("MOVE_ACCEPTED", "GOAL_REACHED", "STOP_CONFIRMED", "COMPLETED"):
                if self.progress and (transition := self.progress.ingest(event, data)):
                    name, step = transition
                    self._record(name, step=step, fields=data)
                if self.phase == "EXECUTING" and event != "COMPLETED":
                    self.detail = event
                    if data.get('telemetry'):
                        self.last_feedback = data['telemetry']

    def cancel(self, reason='OPERATOR_STOP'):
        self._stop.set()
        self.plan = None
        if self.progress and self.executing and self.progress.state == 'EXECUTING':
            self.progress.state = 'STOPPING'
            self.progress.stop_reason = reason
            source = 'operator' if reason in ('EMERGENCY_STOP', 'USER_PAUSE', 'OPERATOR_STOP') else 'supervisor'
            self._record('STOP_REQUESTED', source=source, reason=reason)
        self.phase = ("STOPPING" if self.executing else "CANCELLING") if self.busy else "CANCELLED"
        self.detail = None

    def close(self):
        executing = self.executing
        self.cancel()
        if executing and self._thread is not None:
            self._thread.join(timeout=1)
            self.poll()
        self.history.close()
