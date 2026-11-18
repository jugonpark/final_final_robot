import json
import unittest
import time
from python_controller.communication.esp32_udp import Protocol, TelemetryGuard, UdpClient
from python_controller.models import MotionCommand, ControlDecision
from python_controller.config import load_config


def telemetry(session=123, seq=1, uptime=1000, **changes):
    d = dict(type='telemetry', boot_id=99, session_id=session, last_seq=seq, uptime_ms=uptime,
             mode='NETWORK', state='STOPPED', fault='NONE', cmd_vx=0, cmd_vy=0, cmd_w=0,
             command_age_ms=20, wheel_pwm=[0,0,0], wheel_speed=[0,0,0])
    d.update(changes)
    return json.dumps(d).encode()


class ProtocolTests(unittest.TestCase):
    def test_background_guard_stops_while_main_does_not_update(self):
        class FakeSocket:
            def __init__(self): self.packets=[];self.incoming=[]
            def bind(self,address): pass
            def setblocking(self,value): pass
            def close(self): pass
            def recvfrom(self,n):
                if not self.incoming: raise BlockingIOError()
                return self.incoming.pop(0)
            def sendto(self,data,address): self.packets.append(json.loads(data))
        sockets=[]
        def factory(*args):
            sock=FakeSocket();sockets.append(sock);return sock
        cfg=load_config();cfg['communication'].update(host='127.0.0.1',signs_verified=True)
        c=UdpClient(cfg,socket_factory=factory)
        def wait_for(predicate):
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if predicate(): return
                time.sleep(.005)
            self.fail('Background safety guard did not reach expected state')
        try:
            c.start()
            wait_for(lambda:c.protocol.seq>=1)
            c.rx.incoming.append((telemetry(session=c.protocol.session_id,seq=c.protocol.seq),('127.0.0.1',8888)))
            wait_for(lambda:c.guard.ready(time.monotonic()))
            self.assertTrue(c.arm())
            c.submit(ControlDecision('FOLLOWING',MotionCommand(2,0,0),'FOLLOW',1),time.monotonic())
            wait_for(lambda:any(p['type']=='cmd_vel' for p in c.tx.packets))
            wait_for(lambda:not c.armed)
            self.assertEqual(c.tx.packets[-1]['type'],'stop')
            self.assertEqual(c.reason,'PERCEPTION_LEASE_EXPIRED')
        finally:
            c.close()

    def test_reboot_invalidates_handshake(self):
        g=TelemetryGuard(123,'127.0.0.1',.7)
        g.receive(telemetry(),('127.0.0.1',8888),1,1)
        self.assertTrue(g.ready(1))
        self.assertFalse(g.receive(telemetry(boot_id=100,uptime=2),('127.0.0.1',8888),1.1,1))
        self.assertFalse(g.ready(1.1))

    def test_sequence_never_wraps_and_invalid_session_rejected(self):
        for session in [0,True,-1,0x100000000]:
            with self.assertRaises(ValueError): Protocol(session)
        p=Protocol(123); p.seq=0xfffffffe
        with self.assertRaises(ValueError): p.stop()

    def test_independent_sender_stops_stale_lease_and_requires_rearm(self):
        class FakeSocket:
            def __init__(self): self.packets = []; self.incoming = []
            def recvfrom(self, n):
                if not self.incoming: raise BlockingIOError()
                return self.incoming.pop(0)
            def sendto(self, data, address): self.packets.append(json.loads(data))
        now = [1.]
        cfg = load_config(); cfg['communication']['host'] = '127.0.0.1'
        c = UdpClient(cfg,clock=lambda:now[0])
        c.tx, c.rx = FakeSocket(), FakeSocket()
        c.tick()
        session = c.protocol.session_id
        c.rx.incoming.append((telemetry(session=session),('127.0.0.1',8888)))
        c.tick()
        self.assertTrue(c.arm())
        d = ControlDecision('FOLLOWING',MotionCommand(2,0,.1),'FOLLOW',1)
        c.submit(d,now[0]); c.tick()
        self.assertEqual(c.tx.packets[-1]['type'],'cmd_vel')
        now[0] += .3
        c.tick()
        self.assertEqual(c.tx.packets[-1]['type'],'stop')
        self.assertFalse(c.armed)
        c.submit(d,now[0]); c.tick()
        self.assertEqual(c.tx.packets[-1]['type'],'stop')

    def test_packets_sequence_units_and_limit(self):
        p = Protocol(123)
        self.assertEqual(json.loads(p.stop())['seq'], 1)
        d = json.loads(p.velocity(MotionCommand(99, 0, 99)))
        self.assertEqual((d['type'], d['status'], d['session_id'], d['seq']), ('cmd_vel','SLOW',123,2))
        self.assertEqual((d['vx'], d['w']), (5, .5))
        self.assertEqual(json.loads(p.reset_fault())['type'], 'reset_fault')
        with self.assertRaises(ValueError): p.velocity(MotionCommand(float('nan'),0,0))

    def test_telemetry_stop_handshake_freshness_session_boot_and_fault(self):
        g = TelemetryGuard(123, '127.0.0.1', .7)
        self.assertFalse(g.ready(1))
        self.assertFalse(g.receive(telemetry(), ('127.0.0.2',8888), 1, 1))
        self.assertTrue(g.receive(telemetry(), ('127.0.0.1',8888), 1, 1))
        self.assertTrue(g.ready(1.1))
        self.assertFalse(g.ready(2))
        self.assertFalse(g.receive(telemetry(uptime=1000), ('127.0.0.1',8888), 1.2, 1))
        self.assertFalse(g.receive(telemetry(uptime=1100, fault='MOTOR_STALL'), ('127.0.0.1',8888), 1.3, 1))
        self.assertFalse(g.ready(1.3))

    def test_malformed_or_nonzero_stopped_telemetry_cannot_arm(self):
        for data in [b'{}', b'garbage', telemetry(cmd_vx=1), telemetry(wheel_pwm=[0,1,0]), telemetry(session=5)]:
            g = TelemetryGuard(123, '127.0.0.1', .7)
            g.receive(data, ('127.0.0.1',8888), 1, 1)
            self.assertFalse(g.ready(1))
