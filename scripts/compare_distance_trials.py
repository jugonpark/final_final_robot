"""Compare wheel PI profiles using floor measurements and optional D.I.G. run logs."""

import argparse
import json
import math
from pathlib import Path
from statistics import mean, stdev


def number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return value


def evidence_from_log(path, direction, target_cm):
    with path.open(encoding='utf-8-sig') as log:
        rows = [json.loads(line) for line in log if line.strip()]
    if (not rows or not all(isinstance(row, dict) for row in rows) or
            not rows[0].get('run_id') or any(row.get('run_id') != rows[0]['run_id'] for row in rows) or
            any(row.get('event') == 'ABORTED' for row in rows)):
        raise ValueError('expected one completed dashboard run')

    def single_event(name):
        matches = [(index, row) for index, row in enumerate(rows) if row.get('event') == name]
        if len(matches) != 1:
            raise ValueError(f'expected one {name} event')
        return matches[0]

    approval_index, approval = single_event('APPROVED')
    goal_index, goal = single_event('GOAL_REACHED')
    completed_index, _ = single_event('COMPLETED')
    actions = approval['actions']
    if (approval.get('source') != 'DASHBOARD' or len(actions) != 1 or
            actions[0].get('type') != 'move' or actions[0].get('direction') != direction or
            float(actions[0]['distance_cm']) != target_cm):
        raise ValueError('recorded single move does not match trial direction and distance')
    stops = [row['telemetry'] for row in rows[goal_index + 1:completed_index] if row.get('event') == 'STOP_CONFIRMED']
    telemetry = goal['telemetry']
    if (not approval_index < goal_index < completed_index or len(stops) != 1 or
            any(type(telemetry.get(key)) is not int or telemetry[key] != stops[0].get(key)
                for key in ('boot_id', 'session_id'))):
        raise ValueError('missing post-goal STOP confirmation from the same session')
    if (telemetry.get('state') != 'GOAL_REACHED' or stops[0].get('state') != 'STOPPED' or
            number(telemetry['goal_target_cm'], 'goal target') != target_cm):
        raise ValueError('invalid goal or STOP telemetry')

    wheel_errors = [[], [], []]
    for row in rows[approval_index + 1:goal_index]:
        if row.get('event') != 'TELEMETRY':
            continue
        sample = row['telemetry']
        if (sample.get('state') not in ('DISTANCE_ACTIVE', 'DISTANCE_BRAKING') or sample.get('fault') != 'NONE' or
                any(sample.get(key) != telemetry.get(key) for key in ('boot_id', 'session_id', 'motion_id'))):
            continue
        targets, speeds = sample.get('wheel_target'), sample.get('wheel_speed')
        if not all(isinstance(vector, list) and len(vector) == 3 for vector in (targets, speeds)):
            continue
        for index, (target, speed) in enumerate(zip(targets, speeds, strict=True)):
            wheel_errors[index].append(abs(number(target, 'wheel target') - number(speed, 'wheel speed')))
    encoder = number(telemetry['goal_progress_cm'], 'goal progress')
    tracking = [dict(motor=f'M{index+1}', samples=len(errors), mean_absolute_error_cm_s=round(mean(errors), 3))
                for index, errors in enumerate(wheel_errors) if errors]
    return encoder, tracking


def compare(manifest_path):
    data = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    profiles = data['profiles']
    baseline = data['baseline']
    condition = data['condition']
    evidence = data['evidence']
    if (not isinstance(condition, str) or not condition.strip() or
            evidence not in ('OPERATOR_MEASURED', 'EXAMPLE')):
        raise ValueError('provide a shared test condition and a valid evidence label')
    if not isinstance(profiles, dict) or baseline not in profiles:
        raise ValueError('declare profiles and the baseline profile')
    for name, profile in profiles.items():
        if any(number(profile[key], f'{name} {key}') < 0 for key in ('kp', 'ki')):
            raise ValueError(f'{name}: kp and ki must be nonnegative')

    results = []
    ids, logs = set(), set()
    groups = {}
    for trial in data['trials']:
        trial_id, profile, direction = (trial[key] for key in ('id', 'profile', 'direction'))
        target = number(trial['target_cm'], 'target_cm')
        measured = number(trial['measured_cm'], 'measured_cm')
        if not isinstance(trial_id, str) or not trial_id.strip() or trial_id in ids:
            raise ValueError('trial IDs must be unique nonempty strings')
        ids.add(trial_id)
        if profile not in profiles or direction not in ('forward', 'backward', 'left', 'right') or not 5 <= target <= 50:
            raise ValueError(f'{trial_id}: select a declared profile, direction and 5–50 cm target')
        row = dict(id=trial_id, profile=profile, direction=direction, target_cm=target,
                   measured_cm=measured, error_cm=round(measured - target, 6))
        if 'log' in trial:
            path = (manifest_path.parent / trial['log']).resolve()
            if path in logs:
                raise ValueError(f'{trial_id}: each run log may be used only once')
            logs.add(path)
            try:
                encoder, tracking = evidence_from_log(path, direction, target)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
                raise ValueError(f'{trial_id}: {exc}') from exc
            row.update(encoder_goal_cm=encoder, measured_minus_encoder_cm=round(measured - encoder, 6),
                       wheel_speed_tracking=tracking)
        results.append(row)
        groups.setdefault((direction, target, profile), []).append(row['error_cm'])
    if not results:
        raise ValueError('add at least one trial')

    summary = []
    for (direction, target, profile), errors in sorted(groups.items()):
        baseline_errors = groups.get((direction, target, baseline))
        mae = mean(map(abs, errors))
        summary.append(dict(direction=direction, target_cm=target, profile=profile, trials=len(errors),
                            mean_error_cm=round(mean(errors), 3), mean_absolute_error_cm=round(mae, 3),
                            error_stdev_cm=round(stdev(errors), 3) if len(errors) > 1 else None,
                            mae_change_vs_baseline_cm=round(mae - mean(map(abs, baseline_errors)), 3)
                            if baseline_errors else None))
    return dict(evidence=evidence, condition=condition, baseline=baseline, profiles=profiles,
                trials=results, comparison=summary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path, help='JSON with profile settings and measured floor distances')
    args = parser.parse_args()
    try:
        report = json.dumps(compare(args.manifest), indent=2, allow_nan=False)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        parser.exit(1, f'Calibration report unavailable: {exc}\n')
    print(report)
