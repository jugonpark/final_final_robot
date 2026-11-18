import json
import unittest
from python_controller.communication.esp32_udp import UdpClient,TelemetryGuard
from python_controller.config import load_config
from test_protocol import telemetry


class FakeSocket:
    def __init__(self):self.incoming=[];self.sent=[]
    def recvfrom(self,size):
        if not self.incoming:raise BlockingIOError()
        return self.incoming.pop(0)
    def sendto(self,data,address):self.sent.append(data);return len(data)


class DashboardTelemetryTests(unittest.TestCase):
    def test_tx_success_ack_and_stop_feedback_are_distinct(self):
        cfg=load_config();cfg['communication']['host']='127.0.0.1'
        now=[1.]
        c=UdpClient(cfg,clock=lambda:now[0]);c.tx,c.rx=FakeSocket(),FakeSocket()
        c.tick()
        snapshot=c.snapshot()
        self.assertEqual(snapshot['last_tx']['type'],'stop')
        self.assertFalse(snapshot['connected'])
        self.assertFalse(snapshot['stop_verified'])
        self.assertIsNone(snapshot['acknowledged_command'])
        sent=c.protocol.seq;now[0]+=.1
        c.rx.incoming.append((telemetry(session=c.protocol.session_id,seq=sent),('127.0.0.1',8888)))
        c.tick();snapshot=c.snapshot()
        self.assertTrue(snapshot['connected'])
        self.assertTrue(snapshot['stop_verified'])
        self.assertEqual(snapshot['acknowledged_command']['seq'],sent)
        c.stop('EMERGENCY_STOP')
        self.assertFalse(c.arm())  # An old STOPPED sample cannot verify this new stop request.
        c.tick()
        self.assertFalse(c.arm())
        stopped_seq=c.protocol.seq;now[0]+=.1
        c.rx.incoming.append((telemetry(session=c.protocol.session_id,seq=stopped_seq,uptime=1100),('127.0.0.1',8888)))
        c.tick()
        self.assertTrue(c.arm())
        now[0]+=1
        self.assertFalse(c.snapshot()['stop_verified'])
        self.assertFalse(c.snapshot()['telemetry_valid'])

    def test_optional_wheel_target_and_encoder_fields_must_be_valid_if_present(self):
        for fields in [dict(wheel_target=[1,2]),dict(wheel_target=[float('nan'),0,0]),dict(encoder_count=[1.5,0,0]),dict(encoder_count=[True,0,0])]:
            with self.subTest(fields=fields):
                g=TelemetryGuard(123,'127.0.0.1',.7)
                self.assertFalse(g.receive(telemetry(**fields),('127.0.0.1',8888),1,1))
                self.assertFalse(g.ready(1))
