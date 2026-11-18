"""Low-speed held gestures using distinct, fresh frames."""
from python_controller.communication.esp32_udp import UdpClient, number
from python_controller.communication.motor_test import body_test_command
from python_controller.models import ControlDecision, MotionCommand


DIRECTIONS = ('STOP', 'FORWARD', 'BACKWARD', 'LEFT', 'RIGHT', 'TURN_LEFT', 'TURN_RIGHT')
DEFAULT_MAPPING = {'Open_Palm': 'FORWARD', 'Thumb_Up': 'LEFT', 'Thumb_Down': 'RIGHT',
                   'Closed_Fist': 'STOP', 'Victory': 'BACKWARD'}


class HandDrive:
    def __init__(self, confidence=.7, frames=2, hold=.25, mapping=None):
        self.mapping = dict(DEFAULT_MAPPING if mapping is None else mapping)
        if self.mapping.keys() == DEFAULT_MAPPING.keys() - {'Victory'}:
            self.mapping['Victory'] = 'STOP'
        if (not number(confidence) or not .5 <= confidence <= .99 or type(frames) is not int or
            not 2 <= frames <= 8 or not number(hold) or not .1 <= hold <= 1 or
            self.mapping.keys() != DEFAULT_MAPPING.keys() or self.mapping['Closed_Fist'] != 'STOP' or
            any(direction not in DIRECTIONS for direction in self.mapping.values())):
            raise ValueError('손동작 제어 설정 범위를 확인하세요.')
        self.confidence, self.min_frames, self.hold = confidence, frames, hold
        self.last = None
        self.reset()

    def reset(self):
        self.label, self.first, self.frames = None, None, 0

    def observe(self, vision, timestamp, now, max_age, fresh=True):
        if not fresh or not 0 <= now-timestamp <= max_age or (self.last is not None and timestamp < self.last):
            self.reset()
            return dict(direction='STOP', reason='PERCEPTION_STALE_OR_FAILED', elapsed=0., frames=0)
        raw = vision.get('raw', ())
        hand = raw[0] if vision.get('hand_count') == 1 and len(raw) == 1 else {}
        label, confidence = hand.get('name'), hand.get('confidence')
        direction = self.mapping.get(label, 'STOP')
        valid = number(confidence) and self.confidence <= confidence <= 1
        if direction == 'STOP' or not valid:
            self.reset()
            self.last = timestamp
            return dict(label=label, confidence=confidence, direction='STOP', elapsed=0., frames=0,
                        reason='GESTURE_STOP' if label == 'Closed_Fist' else 'NO_VALID_DRIVE_GESTURE')
        if timestamp != self.last:
            if self.last is None or self.label != label or timestamp - self.last > max_age:
                self.label, self.first, self.frames = label, timestamp, 0
            self.frames += 1
            self.last = timestamp
        elapsed = timestamp-self.first if self.first is not None else 0.
        ready = self.frames >= self.min_frames and elapsed >= self.hold
        return dict(label=label, confidence=confidence, direction=direction if ready else 'STOP',
                    elapsed=elapsed, frames=self.frames, reason='GESTURE_DRIVING' if ready else 'GESTURE_HOLD')


class HandDriveClient(UdpClient):
    allow_all_axes = True

    def submit_gesture(self, status, timestamp, recognized_at):
        direction = status['direction']
        limits = self.cfg['motor_test']
        command = (body_test_command(direction, dict(linear_cm_s=min(limits['linear_cm_s'], 2.5),
                                                       angular_rad_s=min(limits['angular_rad_s'], .2)))
                   if direction != 'STOP' else MotionCommand())
        with self.lock:
            if not self.armed:
                return MotionCommand()
            moving = direction != 'STOP'
            if self.active and not moving:
                self.stop_requested_at = self.clock()
                self.stop_seq = None
            if moving and not self.active:
                data = self.guard.data
                moving = bool(self.stop_seq is not None and self.guard.ready(self.clock()) and
                              data['last_seq'] >= self.stop_seq and self.guard.received_at >= self.stop_requested_at)
            applied = command if moving else MotionCommand()
            self.submit(ControlDecision('GESTURE_OVERRIDE' if moving else 'PAUSED', applied, status['reason'], None),
                        timestamp, recognized_at)
            return applied
