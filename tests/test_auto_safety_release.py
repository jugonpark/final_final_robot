"""Automatic block release policy, exercised with fake time and fake ESP32 only."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from python_controller.control.safety_supervisor import SafetySupervisor
from python_controller.dashboard_runtime import DashboardEngine
from test_radar_integration import PI, radar
import test_watchdog_and_recovery as wire_fixture
from test_watchdog_and_recovery import SOURCE, V2, telemetry


class RadarFreshnessTests(unittest.TestCase):
    def test_silence_is_warning_only_but_preserves_previous_hazard(self):
        gate = SafetySupervisor(PI)
        gate.receive(radar(1), PI, 0)
        gate.rearm(0)
        generation = gate.generation
        stale = gate.snapshot(.31)
        self.assertEqual(stale['state'], 'CLEAR')
        self.assertFalse(stale['blocked'])
        self.assertFalse(stale['fresh'])
        self.assertEqual(stale['warning'], 'RADAR_TIMEOUT')
        self.assertEqual(stale['generation'], generation)
        with self.assertRaises(ValueError):
            gate.rearm(.31)
        gate.receive(radar(2, 'EVADE', epoch=1), PI, .4)
        danger = gate.snapshot(.8)
        self.assertTrue(danger['blocked'])
        self.assertEqual(danger['state'], 'EVADE')
        self.assertEqual(danger['reason'], 'RADAR_EVADE')
        self.assertEqual(danger['warning'], 'RADAR_TIMEOUT')

    def test_clear_timer_resets_on_gaps_reboot_epoch_invalid_and_unknown(self):
        gate = SafetySupervisor(PI)
        gate.receive(radar(1), PI, 0)
        gate.receive(radar(2), PI, .2)
        self.assertAlmostEqual(gate.snapshot(.2)['clear_for_s'], .2)
        gate.receive(radar(3), PI, .6)
        self.assertEqual(gate.snapshot(.6)['clear_for_s'], 0)
        gate.receive(radar(1, boot=2), PI, .7)
        self.assertEqual(gate.snapshot(.7)['clear_for_s'], 0)
        gate.receive(radar(2, boot=2, epoch=1), PI, .8)
        self.assertEqual(gate.snapshot(.8)['clear_for_s'], 0)
        self.assertFalse(gate.receive(b'{}', PI, .9))
        self.assertTrue(gate.snapshot(.9)['blocked'])
        self.assertEqual(gate.snapshot(.9)['state'], 'UNKNOWN')
        gate.receive(radar(3, boot=2, epoch=1), PI, 1)
        self.assertEqual(gate.snapshot(1)['clear_for_s'], 0)
        gate.receive(radar(4, 'UNKNOWN', boot=2, epoch=1), PI, 1.1)
        self.assertEqual(gate.snapshot(1.1)['clear_for_s'], 0)


class AutoSafetyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('No real UDP sockets')).start()
        self.wire = wire_fixture.RecoveryTests()
        self.wire.setUp()
        self.addCleanup(self.wire.doCleanups)
        self.session = self.wire.session
        self.gate = self.session.safety = SafetySupervisor(PI)
        self.sequence = 1
        self.gate.receive(radar(self.sequence), PI, self.wire.now)
        self.wire.ready_v2()
        self.session.rearm_radar()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine = DashboardEngine(self.session.cfg, Path(tmp.name), radar=self.gate, logger=False)
        self.addCleanup(self.engine.close)
        self.engine.client, self.engine.client_owner = self.session, 'DIRECT_CONTROL'
        self.engine.seen_recovery_generation = self.session.recovery_generation
        self.engine.browser_operator_required = True
        self.start = self.wire.now

    def pump(self, elapsed, *, fresh=True, operator=True, receive=True, epoch=0, **changes):
        self.wire.now = self.start + elapsed
        if fresh:
            self.sequence += 1
            self.gate.receive(radar(self.sequence, epoch=epoch), PI, self.wire.now)
        if operator:
            self.engine.operator_alive()
        self.session.controller_alive()
        if receive:
            values = dict(V2, **changes)
            raw = telemetry(session=self.session.protocol.session_id, seq=self.session.protocol.seq,
                            uptime=int(self.wire.now * 10000), **values)
            self.session.rx.incoming.put((raw, SOURCE))
        self.session.tick()
        self.engine.tick(None, self.wire.now)
        self.session.tick()

    def test_estop_releases_at_one_second_only_and_never_replays_input(self):
        self.engine.request('ESTOP')
        old_generation = self.engine.control_generation
        for t in (0, .2, .4, .6, .8, .999):
            self.pump(t)
            self.assertTrue(self.engine.c.estop_latched)
        self.pump(1.)
        self.assertFalse(self.engine.c.estop_latched)
        self.assertEqual(self.engine.auto_recovery['state'], 'READY_NEW_INPUT')
        self.assertGreater(self.engine.control_generation, old_generation)
        self.assertIsNone(self.engine.pending)
        self.assertIsNone(self.engine.web_vision)
        self.assertTrue(self.engine.hand_requires_activation)
        self.assertFalse(self.engine.follow_auto_arm)
        self.assertFalse(self.engine.c.armed)
        self.assertFalse(self.session.armed)
        self.assertFalse(any(p['type'] in ('cmd_move', 'cmd_vel') for p in self.session.tx.sent))

    def test_new_estop_restarts_timer_despite_long_clear(self):
        for t in (0, .2, .4, .6, .8, 1., 1.2):
            self.pump(t)
        self.engine.request('ESTOP')
        self.pump(1.2)
        for t in (1.4, 1.6, 1.8, 2., 2.199):
            self.pump(t)
            self.assertTrue(self.engine.c.estop_latched)
        self.pump(2.201)
        self.assertFalse(self.engine.c.estop_latched)

    def test_operator_loss_and_stale_clear_do_not_satisfy_release(self):
        self.engine.request('ESTOP')
        for t in (0, .2, .4, .6, .8, 1., 1.2):
            self.pump(t, operator=False)
        self.assertTrue(self.engine.c.estop_latched)
        self.assertEqual(self.engine.auto_recovery['state'], 'WAITING_OPERATOR')
        self.pump(1.3)
        for t in (1.5, 1.7, 1.9, 2.1):
            self.pump(t, fresh=False)
        self.assertTrue(self.engine.c.estop_latched)
        self.assertEqual(self.engine.auto_recovery['state'], 'WAITING_CLEAR')
        for t in (2.2, 2.4, 2.6, 2.8, 3., 3.199):
            self.pump(t)
            self.assertTrue(self.engine.c.estop_latched)
        self.pump(3.201)
        self.assertFalse(self.engine.c.estop_latched)

    def test_persistent_hazard_does_not_auto_clear(self):
        self.gate.receive(radar(2, 'EVADE', epoch=1), PI, self.start)
        for t in (0, .2, .4, .6, .8, 1., 1.2):
            self.pump(t, fresh=False)
        self.assertTrue(self.gate.latched)
        self.assertEqual(self.gate.state, 'EVADE')
        self.assertEqual(self.engine.auto_recovery['state'], 'WAITING_CLEAR')
        self.assertFalse(self.session.active)

    def test_radar_silence_does_not_stop_active_manual_or_cancel_llm(self):
        self.engine.operator_alive()
        self.session.begin_hold('FORWARD')
        self.session.tick()
        generation = self.engine.control_generation
        with patch.object(self.engine.language, 'cancel') as cancel:
            for t in (.1, .2, .31, .4, .5):
                self.pump(t, fresh=False)
                self.assertTrue(self.session.active)
            self.assertEqual(self.engine.control_generation, generation)
            cancel.assert_not_called()
        self.assertEqual(self.gate.snapshot(self.wire.now)['warning'], 'RADAR_TIMEOUT')
        self.assertFalse(self.gate.latched)

    def test_radar_timeout_does_not_cancel_active_llm_execution(self):
        self.engine.language.phase = 'EXECUTING'
        self.wire.now = self.start + .31
        self.engine.operator_alive()
        generation = self.engine.control_generation
        with patch.object(self.engine.language, 'cancel') as cancel:
            status = self.engine._sync_radar(self.wire.now)
            self.engine._auto_release_safety(status, self.wire.now)
            cancel.assert_not_called()
        self.assertTrue(self.engine.language.executing)
        self.assertEqual(self.engine.control_generation, generation)
        self.assertFalse(self.gate.latched)
        self.engine.language.phase = 'IDLE'

    def test_old_manual_token_is_invalid_after_automatic_release(self):
        from python_controller.web_dashboard import WebDashboard
        web = WebDashboard(self.engine, None, control_enabled=True)
        token = 'a' * 32
        web.manual_hold = (token, self.session, 'FORWARD', None, 'DIRECT_CONTROL', self.engine.control_generation)
        self.engine.request('ESTOP')
        for t in (0, .2, .4, .6, .8, 1.):
            self.pump(t)
        self.assertFalse(self.engine.c.estop_latched)
        with self.assertRaises(ValueError):
            web._manual_command('manual_hold', {'token': token})
        self.assertFalse(self.session.active)

    def test_profile_controller_conflict_and_python_fatal_do_not_release(self):
        self.engine.request('ESTOP')
        for t in (0, .2, .4, .6, .8):
            self.pump(t)
        for block in ('profile', 'conflict', 'python'):
            with self.subTest(block=block):
                self.pump(1.)
                self.engine.request('ESTOP')
                status = self.session.snapshot()
                if block == 'profile':
                    status['hardware_profile_status'] = 'HARDWARE_PROFILE_MISMATCH'
                elif block == 'conflict':
                    status.update(recovery_state='SESSION_CONFLICT', session_lost=True)
                else:
                    self.engine.control_error = 'CONTROL_SUPERVISOR_FAILED'
                self.engine.auto_release_since = self.start - 2
                self.wire.now = self.start + 1
                self.engine.operator_alive()
                with patch.object(self.session, 'snapshot', return_value=status):
                    self.engine._auto_release_safety(self.gate.snapshot(self.wire.now), self.wire.now)
                self.assertTrue(self.engine.c.estop_latched)
                self.engine.control_error = None

    def test_telemetry_stall_waits_for_fresh_stop_then_releases(self):
        self.pump(.1)
        self.pump(1., receive=False)
        self.assertTrue(self.engine.c.estop_latched)
        for t in (1.2, 1.4, 1.6, 1.8, 2.):
            self.pump(t, receive=False)
        self.assertEqual(self.engine.auto_recovery['state'], 'WAITING_STOP')
        self.assertTrue(self.engine.c.estop_latched)
        self.pump(2.1)
        self.assertFalse(self.engine.c.estop_latched)
        self.assertFalse(self.session.active)
        self.assertFalse(any(p['type'] in ('cmd_move', 'cmd_vel') for p in self.session.tx.sent))

    def test_supported_encoder_fault_recovers_once_then_waits_device_clear(self):
        fault = dict(state='FAULT', fault='ENCODER_JUMP', encoder_good_samples=10)
        with patch.object(self.session, 'recover', wraps=self.session.recover) as recover:
            for t in (0, .2, .4, .6, .8, 1., 1.2, 1.4):
                self.pump(t, **fault)
            self.assertEqual(recover.call_count, 1)
            self.assertTrue(self.engine.c.estop_latched)
            self.assertIn('reset_fault', [p['type'] for p in self.session.tx.sent])
            self.pump(1.6)
            self.pump(1.8)
            self.assertEqual(recover.call_count, 1)
        self.assertFalse(self.engine.c.estop_latched)
        self.assertFalse(self.session.active)
        self.assertFalse(any(p['type'] in ('cmd_move', 'cmd_vel') for p in self.session.tx.sent))

    def test_unsupported_fault_and_unclean_encoder_stay_blocked(self):
        for index, fault in enumerate(('STALL', 'ENCODER_JUMP')):
            with self.subTest(fault=fault):
                self.engine.request('ESTOP')
                for t in (0, .2, .4, .6, .8, 1., 1.2):
                    self.pump(index * 1.5 + t, state='FAULT', fault=fault, encoder_good_samples=2)
                self.assertTrue(self.engine.c.estop_latched)
                self.assertFalse(self.session.active)
        self.assertNotIn('reset_fault', [p['type'] for p in self.session.tx.sent])


if __name__ == '__main__':
    unittest.main()
