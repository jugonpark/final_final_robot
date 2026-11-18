import json
import unittest
from python_controller.config import load_config
from python_controller.models import MotionCommand,ControlDecision
from python_controller.communication.motor_test import MotorTestClient,body_test_command,encoder_delta,save_connection,load_connection
from pathlib import Path
import tempfile
from test_protocol import telemetry


class Socket:
    def __init__(self):self.incoming=[];self.sent=[];self.closed=False
    def recvfrom(self,n):
        if not self.incoming:raise BlockingIOError()
        return self.incoming.pop(0)
    def sendto(self,data,address):self.sent.append(json.loads(data));return len(data)
    def close(self):self.closed=True


class MotorTestTests(unittest.TestCase):
    def setUp(self):
        self.now=[1.]
        self.cfg=load_config();self.cfg['communication']['host']='127.0.0.1'
        self.c=MotorTestClient(self.cfg,clock=lambda:self.now[0],evidence_source='MOCK_UDP')
        self.c.tx,self.c.rx=Socket(),Socket()

    def receive(self,state='STOPPED',command=(0,0,0),counts=(0,0,0),targets=(0,0,0),speeds=(0,0,0),fault='NONE'):
        self.now[0]+=.1
        self.c.operator_alive()
        d=telemetry(session=self.c.protocol.session_id,seq=self.c.protocol.seq,uptime=round(self.now[0]*1000),state=state,
            cmd_vx=command[0],cmd_vy=command[1],cmd_w=command[2],encoder_count=list(counts),wheel_target=list(targets),wheel_speed=list(speeds),fault=fault)
        self.c.rx.incoming.append((d,('127.0.0.1',8888)));self.c.tick()

    def ready(self):
        self.c.tick();self.receive()

    def test_connection_only_stop_then_stationary_heartbeat_never_motion(self):
        self.c.tick();self.assertEqual(self.c.tx.sent[-1]['type'],'stop')
        self.receive()
        self.assertEqual(self.c.tx.sent[-1]['type'],'heartbeat')
        self.assertFalse(self.c.snapshot()['armed'])
        self.assertFalse(any(p['type']=='cmd_vel' for p in self.c.tx.sent))

    def test_confirmation_and_stop_proof_required(self):
        with self.assertRaises(ValueError):self.c.authorize_manual(True,True)
        self.ready()
        for lifted,approved in [(False,True),(True,False)]:
            with self.assertRaises(ValueError):self.c.authorize_manual(lifted,approved)
        self.c.authorize_manual(True,True)
        self.assertTrue(self.c.snapshot()['manual_enabled'])
        self.assertFalse(any(p['type']=='cmd_vel' for p in self.c.tx.sent))

    def test_all_six_directions_bounded_and_lateral_reverse_are_manual_only(self):
        for name in ('FORWARD','BACKWARD','LEFT','RIGHT','TURN_LEFT','TURN_RIGHT'):
            with self.subTest(name=name):
                self.setUp();self.ready();self.c.authorize_manual(True,True)
                self.c.begin_test(name,.4);self.c.tick()
                packet=self.c.tx.sent[-1]
                self.assertEqual(packet['type'],'cmd_vel');self.assertEqual(packet['status'],'SLOW')
                self.assertLessEqual(abs(packet['vx']),5);self.assertLessEqual(abs(packet['vy']),5);self.assertLessEqual(abs(packet['w']),.5)
                self.now[0]+=.41;self.c.operator_alive();self.c.tick()
                self.assertEqual(self.c.tx.sent[-1]['type'],'stop')
                self.assertFalse(self.c.snapshot()['manual_enabled'])
                self.c.submit(ControlDecision('FOLLOWING',MotionCommand(5,0,0),'FOLLOW',1),self.now[0]);self.c.tick()
                self.assertEqual(self.c.tx.sent[-1]['type'],'stop')

    def test_ui_stall_fault_and_telemetry_timeout_latch(self):
        for reason in ('UI','TELEMETRY','FAULT','ESTOP'):
            with self.subTest(reason=reason):
                self.setUp();self.ready();self.c.authorize_manual(True,True);self.c.begin_test('FORWARD',1.5);self.c.tick()
                if reason=='UI':self.now[0]+=.3
                elif reason=='TELEMETRY':self.now[0]+=.6;self.c.operator_alive()
                elif reason=='FAULT':self.receive(fault='MOTOR_STALL')
                else:self.c.stop('EMERGENCY_STOP')
                self.c.tick()
                self.assertEqual(self.c.tx.sent[-1]['type'],'stop')
                self.assertFalse(self.c.snapshot()['manual_enabled'])
                with self.assertRaises(ValueError):self.c.begin_test('LEFT',.4)
                self.receive()
                self.assertTrue(self.c.snapshot()['stop_verified'])
                self.assertFalse(self.c.snapshot()['manual_enabled'])
                with self.assertRaises(ValueError):self.c.begin_test('LEFT',.4)

    def test_wrong_session_or_sequence_cannot_confirm_probe(self):
        self.c.tick()
        self.c.rx.incoming.append((telemetry(session=1,seq=999),('127.0.0.1',8888)))
        self.c.tick()
        self.assertFalse(self.c.snapshot()['connected'])
        self.assertEqual(self.c.tx.sent[-1]['type'],'stop')

    def test_processing_echo_and_encoder_evidence_are_separate(self):
        self.ready();self.c.authorize_manual(True,True);self.c.begin_test('FORWARD',.4);self.c.tick()
        speed=self.cfg['motor_test']['linear_cm_s']
        self.receive('VELOCITY',(speed,0,0),counts=(0,-10,10),targets=(0,-2,2),speeds=(0,-1,1))
        trial=self.c.snapshot()['motor_test']['trial']
        self.assertTrue(trial['command_processed'])
        self.assertEqual(trial['physical_result'],'NOT TESTED')
        self.assertEqual(trial['evidence_source'],'MOCK_UDP')
        self.now[0]+=.41;self.c.operator_alive();self.c.tick();self.receive(counts=(0,-10,10))
        record=self.c.snapshot()['motor_test']['history'][-1]
        self.assertTrue(record['stop_verified'])
        self.assertEqual(record['motors'][0]['encoder_evidence'],'NOT_EXPECTED_TO_ROTATE')
        self.assertEqual(record['motors'][1]['encoder_evidence'],'ENCODER_SIGN_AGREES')
        self.assertEqual(record['motors'][2]['encoder_evidence'],'ENCODER_SIGN_AGREES')
        self.assertEqual(record['physical_result'],'NOT TESTED')

    def test_no_echo_does_not_become_command_processed(self):
        self.ready();self.c.authorize_manual(True,True);self.c.begin_test('FORWARD',.4);self.c.tick()
        self.receive('STOPPED',counts=(0,-10,10))
        self.assertFalse(self.c.snapshot()['motor_test']['trial']['command_processed'])

    def test_unexpected_idle_motion_stops_instead_of_heartbeat(self):
        self.ready();self.receive('VELOCITY',(2,0,0),speeds=(0,-1,1))
        self.assertEqual(self.c.tx.sent[-1]['type'],'stop')

    def test_duration_and_nonfinite_inputs_rejected(self):
        self.ready();self.c.authorize_manual(True,True)
        for duration in (0,-1,100,float('nan')):
            with self.assertRaises(ValueError):self.c.begin_test('FORWARD',duration)
        with self.assertRaises(ValueError):body_test_command('UNKNOWN',self.cfg['motor_test'])
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            path=Path(folder)/'unsafe.json'
            for override in ({'linear_cm_s':100},{'angular_rad_s':2},{'max_duration_s':100},{'send_period_s':.3},{'ui_lease_s':5}):
                path.write_text(json.dumps({'motor_test':override}),encoding='utf-8')
                with self.assertRaises(ValueError):load_config(path)

    def test_opposite_or_missing_encoder_change_is_diagnostic_not_physical_pass(self):
        for counts,label in [((0,10,-10),'ENCODER_SIGN_DISAGREES'),((0,0,0),'NO_ENCODER_CHANGE_OBSERVED')]:
            with self.subTest(counts=counts):
                self.setUp();self.ready();self.c.authorize_manual(True,True);self.c.begin_test('FORWARD',.4);self.c.tick()
                speed=self.cfg['motor_test']['linear_cm_s']
                self.receive('VELOCITY',(speed,0,0),counts=counts,targets=(0,-2,2))
                trial=self.c.snapshot()['motor_test']['trial']
                self.assertEqual(trial['motors'][1]['encoder_evidence'],label)
                self.assertEqual(trial['physical_result'],'NOT TESTED')

    def test_restart_mid_trial_blocks_until_fresh_stop_and_reauthorization(self):
        self.ready();self.c.authorize_manual(True,True);self.c.begin_test('LEFT',.4);self.c.tick()
        self.c.rx.incoming.append((telemetry(session=self.c.protocol.session_id,seq=self.c.protocol.seq,uptime=1,boot_id=100),('127.0.0.1',8888)))
        self.c.tick();self.assertFalse(self.c.snapshot()['manual_enabled'])
        self.assertEqual(self.c.tx.sent[-1]['type'],'stop')
        with self.assertRaises(ValueError):self.c.begin_test('LEFT',.4)

    def test_close_attempts_stop_and_keeps_unverified_trial_record(self):
        self.ready();self.c.authorize_manual(True,True);self.c.begin_test('TURN_LEFT',.4);self.c.tick()
        tx=self.c.tx;self.c.close()
        self.assertEqual(tx.sent[-1]['type'],'stop')
        record=self.c.snapshot()['motor_test']['history'][-1]
        self.assertFalse(record['stop_verified']);self.assertEqual(record['physical_result'],'NOT TESTED')

    def test_physical_observation_requires_completed_trial_and_notes(self):
        self.ready();self.c.authorize_manual(True,True);self.c.begin_test('FORWARD',.4);self.c.tick()
        with self.assertRaises(ValueError):self.c.record_observations(1,['NOT TESTED']*3,'not completed')
        self.c.stop();self.c.tick();self.receive()
        with self.assertRaises(ValueError):self.c.record_observations(1,['MATCHES EXPECTED']*3,'')
        self.c.record_observations(1,['NOT TESTED','OPPOSITE','NO ROTATION'],'Mock operator observation')
        self.assertEqual(self.c.snapshot()['motor_test']['history'][-1]['physical_result'],'OPERATOR_RECORDED')

    def test_ip_save_is_local_only_and_rejects_invalid_addresses(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as folder:
            path=Path(folder)/'connection.json'
            self.assertEqual(load_connection(path),'')
            save_connection(path,'192.168.1.25')
            self.assertEqual(load_connection(path),'192.168.1.25')
            for host in ('not-an-ip','0.0.0.0','224.0.0.1','255.255.255.255'):
                with self.assertRaises(ValueError):save_connection(path,host)

    def test_signed_counter_wrap(self):
        self.assertEqual(encoder_delta(-2147483647,2147483646),3)
