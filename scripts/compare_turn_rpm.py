"""Compare equal-speed left/right turns in one dashboard JSONL log."""

import argparse
import json
import sys
from pathlib import Path
from statistics import median


def compare_turns(path, angular_speed, settle_ms=600):
    runs = {}
    previous = {}
    fields = ('wheel_target_rpm', 'wheel_measured_rpm', 'wheel_pwm',
              'wheel_ff_pwm', 'wheel_pi_trim_pwm')
    with Path(path).open(encoding='utf-8-sig') as log:
        for line in log:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get('event') != 'UDP_RX' or row.get('accepted') is not True:
                continue
            t = row.get('json') or {}
            boot, session, uptime, token = (t.get(k) for k in (
                'boot_id', 'session_id', 'uptime_ms', 'execution_token'))
            if not all(type(v) is int for v in (boot, session, uptime, token)):
                continue
            key = boot, session
            w = t.get('cmd_w')
            if (t.get('state') != 'VELOCITY' or t.get('mode') != 'NETWORK' or
                t.get('control_style') != 'FEEDFORWARD_PI' or t.get('fault') != 'NONE' or
                t.get('encoder_warning') is not False or token <= 0 or
                not isinstance(w, (int, float)) or abs(abs(w)-angular_speed) > .001 or
                t.get('cmd_vx') != 0 or t.get('cmd_vy') != 0):
                previous.pop(key, None)
                continue
            vectors = [t.get(field) for field in fields]
            if not all(isinstance(v, list) and len(v) == 3 and
                       all(type(x) in (int, float) and -10000 < x < 10000 for x in v)
                       for v in vectors):
                continue
            direction = 'left' if w > 0 else 'right'
            marker = token, direction
            if key not in previous or previous[key][0] != marker:
                previous[key] = marker, uptime
            if uptime - previous[key][1] < settle_ms:
                continue
            runs.setdefault(key, {}).setdefault(direction, {})[uptime] = vectors

    result = []
    for (boot, session), groups in runs.items():
        if not all(len(groups.get(direction, {})) >= 3 for direction in ('left', 'right')):
            continue
        for index in range(3):
            row = dict(boot_id=boot, session_id=session, motor=f'M{index+1}')
            for direction, samples in groups.items():
                row[direction] = dict(samples=len(samples))
                row[direction].update({
                    name: round(median(vectors[j][index] for vectors in samples.values()), 3)
                    for j, name in enumerate(fields)
                })
                target = row[direction]['wheel_target_rpm']
                actual = row[direction]['wheel_measured_rpm']
                row[direction]['signed_error_rpm'] = round(target - actual, 3)
            row['left_minus_right_abs_measured_rpm'] = round(
                abs(row['left']['wheel_measured_rpm']) - abs(row['right']['wheel_measured_rpm']), 3)
            result.append(row)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--angular-speed', type=float, default=.2, help='absolute angular speed in rad/s')
    parser.add_argument('--settle-ms', type=int, default=600)
    args = parser.parse_args()
    if not .01 <= args.angular_speed <= .5 or args.settle_ms < 0:
        parser.error('choose an angular speed of 0.01–0.5 rad/s and nonnegative settling time')
    rows = compare_turns(args.log, args.angular_speed, args.settle_ms)
    print(json.dumps(dict(status='COMPARE' if rows else 'INSUFFICIENT_DATA',
                          angular_rad_s=args.angular_speed, motors=rows), indent=2))
    sys.exit(0 if rows else 1)
