import csv
import re
from pathlib import Path
import unittest

from python_controller.communication.robot_session import BENCH_HOLD_SECONDS, RobotSession
from python_controller.config import load_config
from python_controller.control.safety_supervisor import SafetySupervisor
from test_radar_integration import PI, radar
from test_watchdog_and_recovery import FakeSocket, HOST, SOURCE, telemetry


ROOT = Path(__file__).resolve().parents[1]
PCB = dict(hardware_profile='PCB_V19', firmware_version='SIMPLE_V2', pinmap_revision='PCB_V19_GPIO_V1')
BREADBOARD = dict(hardware_profile='BREADBOARD', firmware_version='SIMPLE_V2', pinmap_revision='BREADBOARD_GPIO_V1')


class HardwareProfileTests(unittest.TestCase):
    def setUp(self):
        config = load_config()
        config['communication'].update(host=HOST, hardware_profile='PCB_V19')
        self.now = 1.0
        self.session = RobotSession(config, clock=lambda: self.now, evidence_source='MOCK_UDP')
        self.session.tx, self.session.rx = FakeSocket(), FakeSocket()
        self.addCleanup(self.session.close)
        self.session.tick()

    def receive(self, **identity):
        self.now += .02
        raw = telemetry(session=self.session.protocol.session_id, seq=self.session.protocol.seq,
                        uptime=round(self.now * 1000), **identity)
        self.session.rx.incoming.put((raw, SOURCE))
        self.session.tick()

    def test_unselected_and_legacy_telemetry_block_motion_but_confirm_stop(self):
        self.receive()
        for profile, reason in ((None, 'HARDWARE_PROFILE_UNSELECTED'), ('PCB_V19', 'HARDWARE_PROFILE_UNKNOWN')):
            self.session.hardware_profile = profile
            snapshot = self.session.snapshot()
            self.assertEqual(snapshot['hardware_profile_status'], reason)
            self.assertTrue(snapshot['stop_verified'])
            self.assertFalse(snapshot['ready'])
            self.assertFalse(self.session.arm())

    def test_default_breadboard_uses_legacy_reports_but_still_needs_radar_and_stop(self):
        self.session.hardware_profile = load_config()['communication']['hardware_profile']
        self.assertEqual(self.session.hardware_profile, 'BREADBOARD')
        self.session.safety = SafetySupervisor(PI)
        self.assertFalse(self.session.arm())
        self.receive()
        self.receive()
        self.assertEqual(self.session.snapshot()['hardware_profile_status'], 'LEGACY_BREADBOARD')
        self.assertTrue(self.session.snapshot()['stop_verified'])
        self.assertFalse(self.session.arm())
        self.assertTrue(self.session.safety.receive(radar(1), PI, self.now))
        self.session.rearm_radar()
        self.assertTrue(self.session.arm())

    def test_breadboard_does_not_downgrade_after_partial_or_mismatched_declaration(self):
        self.session.hardware_profile = 'BREADBOARD'
        self.receive()
        self.assertTrue(self.session.snapshot()['ready'])
        self.receive(hardware_profile='BREADBOARD')
        self.assertEqual(self.session.snapshot()['hardware_profile_status'], 'HARDWARE_PROFILE_UNKNOWN')
        self.receive(**PCB)
        self.assertEqual(self.session.snapshot()['hardware_profile_status'], 'HARDWARE_PROFILE_MISMATCH')
        self.receive()
        self.assertEqual(self.session.snapshot()['hardware_profile_status'], 'HARDWARE_PROFILE_UNKNOWN')
        self.assertFalse(self.session.arm())

    def test_incorrect_declaration_cannot_send_motion_or_permitting_heartbeat(self):
        for identity in (BREADBOARD, dict(PCB, hardware_profile='UNRECOGNIZED'), dict(PCB, pinmap_revision='BAD')):
            with self.subTest(identity=identity):
                self.receive(**identity)
                self.assertEqual(self.session.snapshot()['hardware_profile_status'], 'HARDWARE_PROFILE_MISMATCH')
                for kind, fields in (('cmd_vel', {}), ('cmd_move', {}), ('heartbeat', {'motion_permit': True})):
                    self.assertFalse(self.session._send_packet(self.session.protocol.packet(kind, **fields)))
                    self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')

    def test_mismatch_during_motion_requires_new_input_and_stop_after_recovery(self):
        self.receive(**PCB)
        self.assertTrue(self.session.arm())
        self.session.active = True
        self.receive(**BREADBOARD)
        self.assertFalse(self.session.armed)
        self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')
        self.receive(**PCB)
        self.assertEqual(self.session.snapshot()['hardware_profile_status'], 'MATCH')
        self.assertTrue(self.session.snapshot()['stop_verified'])
        self.assertFalse(self.session.armed)
        self.session._start_recovery()
        self.assertEqual(self.session.snapshot()['hardware_profile_status'], 'HARDWARE_PROFILE_UNKNOWN')
        self.assertFalse(self.session.arm())

    def begin_bench(self):
        self.session.safety = SafetySupervisor(PI)
        self.session.radarless_bench = True
        self.receive(**PCB)
        self.receive(**PCB)
        self.session.authorize_bench()
        self.session.operator_alive()
        self.session.begin_hold('FORWARD')
        self.session.tick()

    def test_bench_needs_opt_in_and_matching_stop(self):
        self.session.safety = SafetySupervisor(PI)
        self.receive(**PCB)
        self.receive(**PCB)
        self.assertTrue(self.session.snapshot()['stop_verified'])
        self.assertFalse(self.session.arm())
        with self.assertRaises(ValueError):
            self.session.authorize_bench()
        self.session.radarless_bench = True
        self.receive(**BREADBOARD)
        with self.assertRaises(ValueError):
            self.session.authorize_bench()

    def test_bench_holds_low_speed_and_stops_on_release_or_deadline(self):
        self.begin_bench()
        self.assertTrue(self.session.safety.snapshot(self.now)['blocked'])
        self.assertEqual(self.session.tx.sent[-1]['type'], 'cmd_vel')
        self.assertEqual(self.session.tx.sent[-1]['vx'], 1.0)
        with self.assertRaises(ValueError):
            self.session.select_mode('GESTURE_DIRECT')
        with self.assertRaises(ValueError):
            self.session.distance_socket()
        self.session.end_hold()
        self.session.tick()
        self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')
        self.receive(**PCB)
        self.session.operator_alive()
        self.session.begin_hold('TURN_LEFT')
        self.session.tick()
        self.assertEqual(self.session.tx.sent[-1]['w'], .1)
        deadline = self.now + BENCH_HOLD_SECONDS
        while self.now < deadline:
            self.now += .16
            self.session.operator_alive()
            self.receive(**PCB)
        self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')
        self.assertEqual(self.session.reason, 'RADARLESS_BENCH_HOLD_EXPIRED')
        self.assertFalse(self.session.bench_authorized)

    def test_bench_stops_when_radar_appears_and_does_not_rearm_after_loss(self):
        self.begin_bench()
        self.assertTrue(self.session.safety.receive(radar(1, 'EVADE'), PI, self.now))
        self.session.tick()
        self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')
        self.assertFalse(self.session.bench_authorized)
        self.now += .31
        self.receive(**PCB)
        self.assertEqual(self.session.radar_status()['reason'], 'RADAR_TIMEOUT')
        with self.assertRaises(ValueError):
            self.session.authorize_bench()

    def test_bench_transmitter_rejects_commands_over_the_limit(self):
        self.begin_bench()
        packet = self.session.protocol.packet('cmd_vel', vx=2.0, vy=0, w=0)
        self.assertFalse(self.session._send_packet(packet, moving=True))
        self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')
        self.assertFalse(self.session.bench_authorized)

    def test_browser_hold_requires_fresh_renewal_even_if_qt_is_alive(self):
        self.receive(**PCB)
        self.session.operator_alive()
        self.session.begin_hold('FORWARD', remote=True)
        self.session.tick()
        self.assertEqual(self.session.tx.sent[-1]['type'], 'cmd_vel')
        self.now += .26
        self.session.operator_alive()
        self.session.tick()
        self.assertEqual(self.session.tx.sent[-1]['type'], 'stop')
        self.assertEqual(self.session.reason, 'WEB_HOLD_EXPIRED')
        with self.assertRaises(ValueError):
            self.session.renew_web_hold()

    def test_firmware_assignments_match_all_16_verified_rows(self):
        firmware = ROOT / 'esp32_firmware/control_simple_v2/control_simple_v2/hardware_profiles.h'
        declarations = re.findall(r'HardwarePins HARDWARE_PINS = \{([^;]+)\};', firmware.read_text(encoding='utf-8'))
        with (ROOT / 'docs/hardware/HARDWARE_PROFILE_PINMAP_COMPARE.csv').open(encoding='utf-8', newline='') as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 16)
        self.assertEqual(len(declarations), 2)
        for declaration, column in zip(declarations, ('breadboard_gpio', 'pcb_v19_gpio')):
            pins = [int(pin) for pin in re.findall(r'\d+', declaration)]
            self.assertEqual(pins, [int(row[column]) for row in rows])
            self.assertEqual(len(set(pins)), 16)
        self.assertEqual({row['deployment_match'] for row in rows}, {'UNKNOWN'})
        self.assertEqual(sum(row['profile_comparison'] == 'PROFILE_DELTA' for row in rows), 6)
        self.assertTrue({32, 33}.isdisjoint(int(row['pcb_v19_gpio']) for row in rows))
