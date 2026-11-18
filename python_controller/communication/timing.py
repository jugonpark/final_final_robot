"""Validated timing advertised by the connected firmware."""
HEARTBEAT_PERIOD_S = .1
HEARTBEAT_TIMEOUT_MS = 600
VELOCITY_TTL_MS = 300
CONTROLLER_LEASE_S = .6
LEGACY_TIMEOUT_MS = 300


def firmware_timing(data):
    version = data.get('protocol_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('UNSUPPORTED_PROTOCOL_VERSION')
    if version == 1:
        return 1, LEGACY_TIMEOUT_MS, VELOCITY_TTL_MS
    expected = {'heartbeat_timeout_ms': HEARTBEAT_TIMEOUT_MS,
                'velocity_ttl_ms': VELOCITY_TTL_MS, 'heartbeat_period_ms': 100}
    if any(type(data.get(key)) is not int or data[key] != value for key, value in expected.items()):
        raise ValueError('FIRMWARE_TIMING_MISMATCH')
    return 2, HEARTBEAT_TIMEOUT_MS, VELOCITY_TTL_MS


def command_timing_error(data):
    version, heartbeat_ms, velocity_ms = firmware_timing(data)
    if version == 1:
        return 'COMMAND_AGE_EXCEEDED' if data['command_age_ms'] > LEGACY_TIMEOUT_MS else None
    if data['state'] in ('DISTANCE_ACTIVE', 'DISTANCE_BRAKING', 'VELOCITY'):
        if data.get('controller_alive') is not True or data.get('motion_permit') is not True:
            return 'CONTROLLER_PERMISSION_LOST'
        token = data.get('execution_token')
        if type(token) is not int or not 0 < token <= 0xffffffff:
            return 'INVALID_EXECUTION_TOKEN'
        age = data.get('heartbeat_age_ms')
        if type(age) is not int or not 0 <= age < heartbeat_ms:
            return 'HEARTBEAT_EXPIRED'
    if data['state'] == 'VELOCITY':
        age = data.get('velocity_age_ms')
        if type(age) is not int or not 0 <= age < velocity_ms:
            return 'VELOCITY_EXPIRED'
    return None
