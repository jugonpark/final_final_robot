"""Dedicated PC receive worker; its socket never sends motor commands."""
import socket
import threading
import time


class RadarReceiver:
    def __init__(self, supervisor, clock=time.monotonic):
        self.supervisor = supervisor
        self.clock = clock
        self.done = threading.Event()
        self.sock = self.thread = None

    def start(self):
        if not self.supervisor.source_ip:
            return
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.sock.bind(('', 8891))
            self.sock.settimeout(.05)
        except OSError:
            self.close()
            self.supervisor.receiver_error('RADAR_BIND_FAILED')
            return
        self.thread = threading.Thread(target=self._receive, name='radar-udp-receiver', daemon=True)
        self.thread.start()

    def _receive(self):
        try:
            while not self.done.is_set():
                try:
                    raw, source = self.sock.recvfrom(65535)
                except socket.timeout:
                    self.supervisor.snapshot(self.clock())
                    continue
                self.supervisor.receive(raw, source[0], self.clock())
        except Exception:
            if not self.done.is_set():
                self.supervisor.receiver_error()

    def close(self):
        self.done.set()
        if self.sock is not None:
            self.sock.close()
        if self.thread is not None:
            self.thread.join(timeout=.15)
        self.supervisor.receiver_error('RADAR_RECEIVER_CLOSED')
