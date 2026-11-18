"""Validate distance telemetry without changing the existing driver's accepted states."""
from python_controller.communication.esp32_udp import number, uint
from python_controller.communication.session_sync import SessionSync
from python_controller.diagnostics import safe_payload
from python_controller.communication.timing import command_timing_error
from python_controller.planning.validator import strict_json_object


ACTIVE_STATES = ("DISTANCE_ACTIVE", "DISTANCE_BRAKING")
GOAL_STATES = (*ACTIVE_STATES, "GOAL_REACHED")
GOAL_FIELDS = ("goal_target_cm", "goal_progress_cm", "remaining_cm", "lateral_error_cm", "overshoot_cm")
COMMAND_FIELDS = ("cmd_vx", "cmd_vy", "cmd_w")
COUNTERS = ("boot_id", "session_id", "last_seq", "uptime_ms", "command_age_ms")
FAULTS = {"WIFI_LOSS", "CMD_TIMEOUT", "BAD_PACKET", "SPEED_LIMIT", "ENCODER_JUMP",
          "ENCODER_INVALID", "ODOMETRY_INVALID", "PATH_DEVIATION", "CONTROL_TIMING", "MOTOR_STALL",
          "HEARTBEAT_TIMEOUT","VELOCITY_EXPIRED","GOAL_TIMEOUT","CONTROLLER_UNAVAILABLE"}
LOG_FIELDS = (*COUNTERS, *COMMAND_FIELDS, *GOAL_FIELDS, "mode", "state", "fault", "motion_id",
              "goal_active", "goal_reached", "wheel_target", "wheel_speed", "wheel_pwm", "encoder_count")


class TelemetryError(ValueError):
    pass


def stationary(data):
    return (all(data[k] == 0 for k in COMMAND_FIELDS) and all(pwm == 0 for pwm in data["wheel_pwm"])
            and all(abs(speed) < .3 for speed in data["wheel_speed"]))


class GoalTelemetryGuard:
    def __init__(self, source, session_id, timeout, *, previous=None):
        self.source = source
        self.session_id = session_id
        self.timeout = timeout
        self.boot_id = self.last_uptime = self.last_seq = self.echo_sent_at = None
        self.session_sync = SessionSync(previous)
        self.observed = None
        self.rejection = None

    def fresh(self, now):
        return self.echo_sent_at is not None and 0 <= now - self.echo_sent_at < self.timeout

    def receive(self, raw, source, now, sent, *, abort_stop=False):
        self.observed = self.rejection = None
        if source != self.source:
            return None
        try:
            if len(raw) > 2048:
                raise ValueError("oversize")
            data = strict_json_object(raw.decode("utf-8"))
            self.observed = safe_payload(data)
            self._validate_fields(data, fault_stop=abort_stop and self.boot_id is not None)
        except TelemetryError:
            raise
        except (ValueError, RecursionError, OverflowError):
            raise TelemetryError("MALFORMED_TELEMETRY") from None
        self.observed = safe_payload(data)
        if not self.session_sync.boot_matches(data) or (self.boot_id is not None and data["boot_id"] != self.boot_id):
            raise TelemetryError("ESP32_REBOOT")
        if data["session_id"] != self.session_id:
            reason = self.session_sync.foreign(data, data["state"] == "STOPPED" and stationary(data))
            if reason != "PREVIOUS_SESSION_DURING_STOP":
                raise TelemetryError(reason)
            self.rejection = reason
            return None
        seq = data["last_seq"]
        if seq not in sent:
            raise TelemetryError("SEQUENCE_NOT_SENT")
        if abort_stop and sent[seq][0] != "stop":
            return None
        if self.last_uptime is not None and (data["uptime_ms"] <= self.last_uptime or seq < self.last_seq):
            return None
        try:
            timing_error = command_timing_error(data)
        except ValueError as exc:
            raise TelemetryError(str(exc)) from None
        if timing_error:
            raise TelemetryError(timing_error)
        if not 0 <= now - sent[seq][1] < self.timeout:
            return None
        self.boot_id = data["boot_id"]
        self.session_sync.proven = True
        self.last_uptime, self.last_seq, self.echo_sent_at = data["uptime_ms"], seq, sent[seq][1]
        return self.observed

    @staticmethod
    def _validate_fields(data, *, fault_stop=False):
        if data.get("type") != "telemetry" or not all(uint(data.get(k)) for k in COUNTERS) or not data["boot_id"]:
            raise TelemetryError("INVALID_TELEMETRY_HEADER")
        if data.get("mode") != "NETWORK":
            raise TelemetryError("MODE_NOT_NETWORK")
        fault = data.get("fault")
        state = data.get("state")
        stopped_fault = fault_stop and state == "FAULT" and isinstance(fault, str) and fault in FAULTS
        if fault != "NONE" and not stopped_fault:
            raise TelemetryError(f"ESP32_FAULT_{fault}" if isinstance(fault, str) and fault in FAULTS
                                 else "UNKNOWN_ESP32_FAULT")
        if not stopped_fault and state not in ("STOPPED", *GOAL_STATES):
            raise TelemetryError("UNEXPECTED_ROBOT_STATE")
        if not all(number(data.get(k)) for k in COMMAND_FIELDS):
            raise TelemetryError("INVALID_COMMAND_FEEDBACK")
        for field in ("wheel_pwm", "wheel_speed", "encoder_count"):
            values = data.get(field)
            if (not isinstance(values, list) or len(values) != 3
                    or not all(number(item) if field == "wheel_speed" else
                               type(item) is int and -(2**31) <= item < 2**31 for item in values)):
                raise TelemetryError("INVALID_WHEEL_FEEDBACK")
        if data.get("goal_reached") is not (state == "GOAL_REACHED") or (
                "goal_active" in data and data["goal_active"] is not (state in ACTIVE_STATES)):
            raise TelemetryError("INVALID_GOAL_FLAGS")
        motion_id = data.get("motion_id")
        if not (uint(motion_id) and motion_id < 2**31) and (state in GOAL_STATES or motion_id is not None):
            raise TelemetryError("INVALID_MOTION_ID")
        for field in GOAL_FIELDS:
            value = data.get(field)
            if not number(value) and (state in GOAL_STATES or value is not None):
                raise TelemetryError("INVALID_GOAL_FIELDS")
