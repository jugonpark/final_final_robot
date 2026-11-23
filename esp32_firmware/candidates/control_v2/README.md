# control_v2 isolated ESP32 candidate

This candidate is not uploaded. Reference firmware and llm_ab_v1 are unchanged.
No local credentials are copied. The build uses dummy secrets only under
`.tmp/control-v2/sketch/control_v2/secrets.h`.

## Wire contract

- Telemetry advertises capability protocol_version=2 and 100/600/300 ms timing.
  A typed STOP with protocol_version=2 establishes v2 mode for that session.
  A client probing with a legacy STOP must then send a v2 STOP before v2 motion.
- An unversioned legacy client retains its original 300 ms accepted-packet watchdog.
  A legacy packet cannot downgrade an established v2 session's lifetime rules.
- V2 motion registers a positive, monotonically increasing execution_token.
  A new command initializes one 600 ms heartbeat grace window. Matching heartbeat
  renews only that clock; cmd_vel alone renews its separate 300 ms velocity TTL.
- A velocity refresh may change velocity under the same active token. A distance
  retry uses a newer sequence and identical token, motion_id and content; it does
  not reset progress or the whole-goal deadline.
- STOP cuts output even with an old sequence, closes active/observed tokens and
  clears queued motion. Completion, watchdog and fault stops also retire tokens.
  A heartbeat never activates motion or revives a retired token.
- A different session heartbeat cannot change session ownership. A new session
  must establish STOP first. Same-session STOP cannot downgrade v2 to legacy.
- Deadlines use local unsigned elapsed arithmetic. Expiry is at >=600 ms for
  heartbeat, >=300 ms for velocity. The next control opportunity applies stop.
- Whole-goal timeout is 2 * distance/requested-limited-speed + 3 seconds, separate
  from heartbeat; this is a candidate bound requiring physical validation.
- Added fault names: HEARTBEAT_TIMEOUT, VELOCITY_EXPIRED, GOAL_TIMEOUT.

## Manual mode isolation

The existing llm_ab_v1 Mode Guard is carried forward before command acceptance.
Network motion, heartbeat, reset and profile packets are rejected in MANUAL_TEST
without changing the session, mailbox, fault or controller address. A valid network
STOP still cuts output, disables manual PWM and retires the current token, preserving
the existing session, fault code/time and controller IP. Serial commands keep their
`fromNetwork=false` path. Malformed UDP packets retain the existing BAD_PACKET path.

## Encoder policy

The candidate starts in FILTERED_TEST. A same-session stationary
`set_test_profile` command can select STRICT or FILTERED_TEST. Telemetry echoes
the applied policy.

For a V2 SLOW velocity command, FILTERED_TEST rejects up to four contaminated
ticks in the most recent ten control ticks. Valid wheels keep their normal PID
updates. A wheel with a rejected sample holds its prior PWM in the target
direction, capped at the existing start-assist PWM, and skips that wheel's PID
update. Odometry skips the contaminated tick. A fifth bad tick stops and retires
the token. Ten consecutive good samples clear a recoverable encoder fault at
rest. The next trial requires fresh input. In filtered mode, a rejected sample
while idle remains a warning until another motion is requested.

A distance goal aborts on its first invalid sample under either policy and cannot
report GOAL_REACHED from that sample. A STRICT latched fault still requires
explicit reset_fault plus stationary recovery/handshake.

Telemetry carries the latest rejected sample's timestamp, raw dt, wheel mask,
limit, counts, delta and prior PWM. The most recent stopping fault and its
timestamp remain available after the active fault clears. A new STOP can trigger
a telemetry response with a 50 ms minimum interval between sends. Normal
telemetry continues at 200 ms.
The signed encoder counter rollover path uses unsigned counters/modular deltas.
Pins, CPR, geometry, PID, feed-forward calibration and PWM limits are unchanged.

## Verification and limits

`python tests/test_control_v2_firmware.py -v` compiles actual production C++ helper
static assertions with the installed ESP32 compiler. A separate source invariant
checks the Mode Guard is before acceptance and preserves manual STOP fault/session
isolation; this is not an executed manual-mode simulation. These are compile-only checks
for clocks, rollover and encoder filtering; they do not execute MCU code.
The candidate is separately built with Arduino CLI for esp32:esp32:esp32. Build
artifacts and log are under `.tmp/control-v2`. No upload, serial interaction, RF,
physical encoder calibration or motor run is part of this change.

Heartbeat/token reception, task scheduling, first-fault diagnostics and physical
stop latency still require hardware verification. A CPU/task hang is not bounded
by these software watchdogs. Policy thresholds are test hypotheses, not calibrated
hardware limits. Telemetry is serialized only when the complete JSON is <2048 bytes;
it is never sent truncated.

Earlier local build after carrying forward the Mode Guard: 956,767 bytes flash
(72%) and 50,044 bytes static RAM (15%). Both the C++ helper compile check and
the Mode Guard source invariant check pass. Use `.tmp/control-v2/source_hashes.json`
and `compile.log` when reviewing the build. No hardware validation was performed.

The subsequent telemetry change reads wheel speed and RPM from the encoder sample
even during a fault. The compile-only image in `.tmp/bench-turn-v2/output` uses
956,759 bytes flash and 50,044 bytes static RAM. See the [bench trial record](../../../docs/verification/BENCH_TURN_20261118.md)
for hardware observations with the previously installed firmware.
