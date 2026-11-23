"""Latched radar interlock, shared with the final motor-packet send path."""
import json
import threading


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('Duplicate radar field')
    return fields


class SafetySupervisor:
    def __init__(self, source_ip, timeout_s=.30):
        self.source_ip = source_ip
        self.timeout = timeout_s
        self.lock = threading.RLock()
        self.boot_id = self.seq = self.epoch = None
        self.retired_boots = set()
        self.received_at = None
        self.state = 'UNKNOWN'
        self.reason = 'RADAR_NO_DATA' if source_ip else 'RADAR_NOT_CONFIGURED'
        self.latched = True
        self.generation = 0
        self.minimum = None
        self.points = 0

    def _trip(self, reason):
        if not self.latched or self.reason != reason:
            self.generation += 1
        self.latched = True
        self.reason = reason

    def receiver_error(self, reason='RADAR_RX_ERROR'):
        with self.lock:
            self.state = 'UNKNOWN'
            self.received_at = None
            self.minimum = None
            self.points = 0
            self._trip(reason)

    def _expire(self, now):
        if self.received_at is not None and now - self.received_at >= self.timeout:
            self.receiver_error('RADAR_TIMEOUT')

    def receive(self, raw, source_ip, now):
        if not self.source_ip or source_ip != self.source_ip:
            return False
        with self.lock:
            self._expire(now)
            try:
                if len(raw) > 512:
                    raise ValueError
                packet = json.loads(raw, object_pairs_hook=_unique_fields)
                b, seq, epoch = (packet[k] for k in ('boot_id', 'seq', 'hazard_epoch'))
                state, valid = packet['state'], packet['sensor_valid']
                minimum, points = packet['min_distance_m'], packet['danger_points']
                if (packet['type'] != 'radar_status' or type(packet['protocol_version']) is not int or
                    packet['protocol_version'] != 1 or type(b) is not int or not 0 < b <= 0xffffffff or
                    type(seq) is not int or not 0 < seq <= 0xffffffff or
                    type(epoch) is not int or not 0 <= epoch <= 0xffffffff or
                    state not in ('CLEAR', 'EVADE', 'UNKNOWN') or type(valid) is not bool or
                    valid != (state != 'UNKNOWN') or
                    type(points) is not int or not 0 <= points <= 4096 or
                    (minimum is not None and
                     (type(minimum) not in (int, float) or not 0 <= minimum <= 100))):
                    raise ValueError
            except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
                self.receiver_error('RADAR_BAD_PACKET')
                return False
            if b in self.retired_boots or (b == self.boot_id and (seq <= self.seq or epoch < self.epoch)):
                return False
            if self.boot_id is not None and b != self.boot_id:
                if len(self.retired_boots) >= 256:
                    self.receiver_error('RADAR_REBOOT_LIMIT')
                    return False
                self.retired_boots.add(self.boot_id)
                self._trip('RADAR_REBOOT')
            elif b == self.boot_id and epoch != self.epoch:
                self._trip('RADAR_HAZARD_REPORTED')
            self.boot_id, self.seq, self.epoch = b, seq, epoch
            self.received_at = now
            self.state, self.minimum, self.points = state, minimum, points
            if state != 'CLEAR':
                self._trip('RADAR_EVADE' if state == 'EVADE' else 'RADAR_SENSOR_UNKNOWN')
            return True

    def snapshot(self, now):
        with self.lock:
            self._expire(now)
            return dict(state=self.state, blocked=self.latched, reason=self.reason,
                        generation=self.generation, boot_id=self.boot_id, seq=self.seq,
                        age_s=None if self.received_at is None else max(0., now-self.received_at),
                        min_distance_m=self.minimum, danger_points=self.points)

    def rearm(self, now):
        with self.lock:
            self._expire(now)
            if self.state != 'CLEAR':
                raise ValueError('Pi에서 유효한 CLEAR를 받을 때까지 이동할 수 없습니다.')
            self.latched = False
            self.reason = 'RADAR_CLEAR'
