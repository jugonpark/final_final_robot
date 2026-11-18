"""A bounded STOP-only session acquisition, shared by the real dashboard modes."""
def stopped_report(data):
    pwm, speed = data.get("wheel_pwm"), data.get("wheel_speed")
    return (data.get("state") == "STOPPED" and
            all(type(data.get(key)) in (int, float) and data[key] == 0
                for key in ("cmd_vx", "cmd_vy", "cmd_w")) and
            isinstance(pwm, list) and len(pwm) == 3 and all(type(v) in (int, float) and v == 0 for v in pwm) and
            isinstance(speed, list) and len(speed) == 3 and
            all(type(v) in (int, float) and -.3 < v < .3 for v in speed))


class SessionSync:
    def __init__(self, previous=None):
        self.previous = previous
        self.proven = False
        self.observed = None

    def foreign(self, data, stopped):
        if self.proven or not stopped or data.get("mode") != "NETWORK" or data.get("fault") != "NONE":
            return "SESSION_MISMATCH"
        if self.previous:
            if data.get("boot_id") != self.previous["boot_id"]:
                return "ESP32_REBOOT"
            if (data.get("session_id") != self.previous["tx_session_id"] or
                    data.get("last_seq", -1) > self.previous["tx_seq"]):
                return "SESSION_MISMATCH"
            return "PREVIOUS_SESSION_DURING_STOP"
        candidate = (data.get("session_id"), data.get("boot_id"), data.get("last_seq"))
        if self.observed is None:
            self.observed = candidate
        if candidate[:2] == self.observed[:2] and candidate[2] <= self.observed[2]:
            return "PREVIOUS_SESSION_DURING_STOP"
        return "SESSION_MISMATCH"

    def boot_matches(self, data):
        expected = self.previous["boot_id"] if self.previous else (
            self.observed[1] if self.observed is not None else None
        )
        return expected is None or data.get("boot_id") == expected


def session_fields(owner, session_id, seq, telemetry=None, reason=None, *, stop_sent=False, stop_verified=False):
    data = telemetry or {}
    return dict(active_control_mode=owner, controller_owner=owner,
                tx_session_id=session_id, tx_seq=seq, rx_session_id=data.get("session_id"),
                boot_id=data.get("boot_id"), last_seq=data.get("last_seq"),
                telemetry_state=data.get("state"), fault=data.get("fault"),
                session_mismatch_reason=reason, stop_sent=stop_sent, stop_verified=stop_verified)
