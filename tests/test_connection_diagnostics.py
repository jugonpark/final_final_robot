import json
import unittest
from unittest.mock import patch
from python_controller.config import load_config
from python_controller.communication.esp32_udp import TelemetryGuard
from python_controller.diagnostics import diagnostic_report,safe_payload,inspect_network,save_report
from test_protocol import telemetry
from test_motor_test import Socket


class ConnectionDiagnosticTests(unittest.TestCase):
    def test_udp_events_do_not_starve_periodic_state_snapshots(self):
        import tempfile
        from pathlib import Path
        from python_controller.monitoring import SessionLogger
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            logger=SessionLogger(folder,'HARDWARE','MOCK_UDP')
            with patch('python_controller.monitoring.time.monotonic',side_effect=[1.,1.11,1.12]):
                logger.write({'event':'SNAPSHOT'})
                logger.write({'event':'UDP_TX'},True)
                logger.write({'event':'SNAPSHOT'})
            logger.close()
            records=[json.loads(x) for x in logger.path.read_text().splitlines()]
            self.assertEqual([r['event'] for r in records],['SNAPSHOT','UDP_TX','SNAPSHOT'])

    def test_manual_mode_is_visible_without_becoming_valid(self):
        g=TelemetryGuard(123,'127.0.0.1',.5)
        self.assertFalse(g.receive(telemetry(mode='MANUAL_TEST'),('127.0.0.1',8888),1,1))
        self.assertFalse(g.ready(1))
        self.assertEqual(g.observed['mode'],'MANUAL_TEST')
        self.assertEqual(g.detail,'MODE_NOT_NETWORK')

    def test_session_and_sequence_rejections_are_specific(self):
        for data,expected in [(telemetry(session=456),'SESSION_MISMATCH'),(telemetry(seq=8),'SEQUENCE_NOT_SENT')]:
            g=TelemetryGuard(123,'127.0.0.1',.5)
            self.assertFalse(g.receive(data,('127.0.0.1',8888),1,1))
            self.assertEqual(g.detail,expected)

    def test_payload_filters_secrets_and_nonfinite_fields(self):
        filtered=safe_payload({'mode':'NETWORK','password':'private','ssid':'private','wheel_speed':[float('nan'),0,0]})
        self.assertNotIn('password',filtered);self.assertNotIn('ssid',filtered)
        self.assertIsNone(filtered['wheel_speed'][0])
        json.dumps(filtered,allow_nan=False)
        self.assertIsNone(safe_payload({'last_seq':10**500})['last_seq'])
        g=TelemetryGuard(123,'127.0.0.1',.5)
        packet=json.loads(telemetry());packet.update(password='private',ssid='private')
        self.assertTrue(g.receive(json.dumps(packet).encode(),('127.0.0.1',8888),1,1))
        self.assertNotIn('password',g.data);self.assertNotIn('ssid',g.data)

    def test_invalid_ip_cannot_be_reported_as_ready(self):
        for host in ('old-device','0.0.0.0','255.255.255.255'):
            r=diagnostic_report(host,{})
            self.assertEqual(r['steps'][0]['status'],'FAIL')
            self.assertEqual(r['steps'][5]['status'],'NOT_TESTED')

    def test_network_probe_never_sends_a_udp_payload(self):
        import socket
        class PassiveSocket:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def connect(self,address):self.address=address
            def getsockname(self):return ('10.0.0.2',55000)
            def bind(self,address):self.bound=address
            def sendto(self,*args):raise AssertionError('Passive diagnostics sent UDP')
        with patch('socket.socket',side_effect=lambda *args:PassiveSocket()),patch('python_controller.diagnostics.os.name','posix'):
            r=inspect_network('10.0.0.3')
        self.assertEqual(r['motor_packets_sent'],0);self.assertTrue(r['port_available'])
        self.assertEqual(r['selected_local_ip'],'10.0.0.2')

    def test_network_port_collision_is_retained_without_motion(self):
        class BusySocket:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def connect(self,address):pass
            def getsockname(self):return ('10.0.0.2',55000)
            def bind(self,address):raise OSError(10048,'port used')
        with patch('socket.socket',side_effect=lambda *args:BusySocket()),patch('python_controller.diagnostics.os.name','posix'):
            r=inspect_network('10.0.0.3')
        self.assertFalse(r['port_available']);self.assertEqual(r['bind_error_code'],10048)
        self.assertEqual(r['motor_packets_sent'],0)

    def test_send_error_records_attempt_and_os_error_without_an_ack(self):
        from python_controller.communication.motor_test import MotorTestClient
        config=load_config();config['communication']['host']='127.0.0.1'
        c=MotorTestClient(config)
        class FailedTx:
            def sendto(self,*args):raise OSError(10051,'no network')
        c.tx=FailedTx()
        with self.assertRaises(OSError):c._send_packet(c.protocol.stop())
        event=c.drain_transport_events()[-1]
        self.assertFalse(event['socket_send_completed']);self.assertEqual(event['error_code'],10051)
        self.assertIsNone(c.snapshot()['last_tx'])
        self.assertEqual(c.snapshot()['tx_error_details']['error_code'],10051)

    def test_receive_socket_error_latches_stop_and_retains_cause(self):
        from python_controller.communication.motor_test import MotorTestClient
        config=load_config();config['communication']['host']='127.0.0.1'
        c=MotorTestClient(config)
        class FailedRx:
            def recvfrom(self,*args):raise OSError(10054,'receive error')
        c.tx=Socket();c.rx=FailedRx()
        c._loop()
        snapshot=c.snapshot()
        self.assertFalse(snapshot['connected']);self.assertFalse(snapshot['manual_enabled'])
        self.assertEqual(snapshot['guard_error']['error_code'],10054)
        self.assertEqual(c.tx.sent[-1]['type'],'stop')
        self.assertTrue(any(e['event']=='UDP_GUARD_FAILED' for e in c.drain_transport_events()))

    def test_motor_pwm_diagnosis_uses_same_wheel_and_survives_summary(self):
        trial={'command_processed':True,'motors':[{'motor':2,'peak_pwm':0,'encoder_evidence':'NO_ENCODER_CHANGE_OBSERVED'},
                {'motor':3,'peak_pwm':45,'encoder_evidence':'NO_ENCODER_CHANGE_OBSERVED'}]}
        findings=diagnostic_report('127.0.0.1',{},trial=trial)['findings']
        by_motor={f['evidence']['motor']:f['code'] for f in findings if 'motor' in f['evidence']}
        self.assertEqual(by_motor[2],'ZERO_PWM_AND_NO_ENCODER')
        self.assertEqual(by_motor[3],'PWM_WITHOUT_ENCODER_CHANGE')

    def test_export_includes_failures_and_raw_evidence_in_both_formats(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            r=diagnostic_report('127.0.0.1',{'last_tx':{'type':'stop','seq':3},'rx_bound':True},
                attempt={'status':'STARTED','phase':'RX_BOUND'})
            p=save_report(folder,r)
            self.assertEqual(json.loads(p.read_text(encoding='utf-8'))['transport']['last_tx']['seq'],3)
            md=p.with_suffix('.md').read_text(encoding='utf-8')
            self.assertIn('UDP 수신 대기',md);self.assertIn('"last_tx"',md);self.assertIn('미검증',md)

    def test_no_connection_is_not_a_motor_power_diagnosis(self):
        r=diagnostic_report('10.232.69.103',{'connected':False,'telemetry':None,'last_tx':None},None,None)
        self.assertEqual(r['steps'][0]['status'],'OK')
        self.assertEqual(r['steps'][1]['status'],'NOT_TESTED')
        self.assertTrue(all(f['certainty']!='CONFIRMED_HARDWARE_CAUSE' for f in r['findings']))
        self.assertEqual(r['physical_motor_result'],'NOT TESTED')

    def test_mode_rejection_and_port_collision_are_reported(self):
        t={'connected':False,'telemetry':None,'last_rx_json':{'mode':'MANUAL_TEST'},'rx_packets':1,'last_rx_trusted':True,'validation_detail':'MODE_NOT_NETWORK'}
        r=diagnostic_report('127.0.0.1',t,None,None)
        self.assertEqual(r['steps'][4]['status'],'FAIL')
        r=diagnostic_report('127.0.0.1',{},None,{'status':'FAILED','phase':'RX_BIND','error_code':10048})
        self.assertEqual(r['steps'][1]['status'],'FAIL')
        self.assertIn('8889',r['steps'][1]['message'])

    def test_zero_pwm_or_no_encoder_only_produces_hypotheses(self):
        trial={'command_processed':True,'physical_result':'NOT TESTED','motors':[{'motor':2,'encoder_evidence':'NO_ENCODER_CHANGE_OBSERVED','encoder_delta':0}],
               'samples':[{'wheel_target':[0,-2,2],'wheel_pwm':[0,-40,40],'encoder_count':[0,0,0]}]}
        r=diagnostic_report('127.0.0.1',{'connected':True,'telemetry_valid':True,'telemetry':{'mode':'NETWORK','fault':'NONE','state':'STOPPED'}},None,None,trial)
        self.assertTrue(any(f['code']=='PWM_WITHOUT_ENCODER_CHANGE' for f in r['findings']))
        self.assertEqual(r['physical_motor_result'],'NOT TESTED')

    def test_port_bind_failure_is_logged_while_mode_stays_simulation(self):
        from pathlib import Path
        import tempfile
        from python_controller.dashboard_runtime import DashboardEngine
        class FailClient:
            def __init__(self,c):pass
            def start(self):raise OSError(10048,'port used')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            e=DashboardEngine(load_config(),folder,client_factory=FailClient)
            try:
                with self.assertRaises(OSError):e.connect_motor_test('127.0.0.1',True)
                self.assertEqual(e.mode,'SIMULATION')
                self.assertEqual(e.connection_attempt['status'],'FAILED')
            finally:e.close()
            records=[json.loads(x) for x in e.logger.path.read_text().splitlines()]
            self.assertTrue(any(r.get('event')=='CONNECTION_FAILED' for r in records))
