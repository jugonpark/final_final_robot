#pragma once
#include <stdint.h>
#include "robot_config.h"

// Unsigned elapsed arithmetic preserves watchdog behavior across millis wrap.
constexpr bool lifetimeExpired(uint32_t now, uint32_t received, uint32_t limit) {
    return uint32_t(now - received) >= limit;
}
constexpr int64_t encoderWrappedDelta(int32_t current, int32_t previous) {
    return uint32_t(uint32_t(current) - uint32_t(previous)) <= INT32_MAX
        ? int64_t(uint32_t(uint32_t(current) - uint32_t(previous)))
        : int64_t(uint32_t(uint32_t(current) - uint32_t(previous))) - INT64_C(4294967296);
}
constexpr uint16_t nextEncoderWindow(uint16_t window, bool valid) {
    return uint16_t((window << 1) | (valid ? 0 : 1)) &
           ((1U << robot_config::ENCODER_BAD_WINDOW_TICKS) - 1U);
}
constexpr uint8_t encoderWindowBadCount(uint16_t window) {
    uint8_t count = 0;
    for (uint8_t bit = 0; bit < robot_config::ENCODER_BAD_WINDOW_TICKS; ++bit)
        count += (window >> bit) & 1U;
    return count;
}
constexpr bool mayHoldEncoderTick(bool filtered, bool slowVelocity, uint8_t recentBad) {
    return filtered && slowVelocity && recentBad > 0 &&
           recentBad <= robot_config::ENCODER_FILTERED_MAX_BAD_TICKS;
}
static_assert(!lifetimeExpired(699, 100, 600), "heartbeat must survive 599 ms");
static_assert(lifetimeExpired(700, 100, 600), "heartbeat expires at 600 ms");
static_assert(lifetimeExpired(400, 100, 300), "velocity expires independently at 300 ms");
static_assert(lifetimeExpired(84, UINT32_MAX - 15, 100), "millis rollover retains expiry");
static_assert(encoderWrappedDelta(INT32_MIN, INT32_MAX) == 1, "positive count rollover");
static_assert(encoderWrappedDelta(INT32_MAX, INT32_MIN) == -1, "negative count rollover");
static_assert(encoderWrappedDelta(1000000, -1000000) == 2000000, "large raw jump retained");
static_assert(mayHoldEncoderTick(true, true, 1), "reject an isolated bad sample");
static_assert(mayHoldEncoderTick(true, true, 4), "tolerate a short low-speed burst");
static_assert(!mayHoldEncoderTick(true, true, 5), "stop when feedback stays unreliable");
static_assert(!mayHoldEncoderTick(true, false, 1), "distance and non-slow commands require clean feedback");
static_assert(!mayHoldEncoderTick(false, true, 1), "STRICT must stop immediately");
static_assert(encoderWindowBadCount(nextEncoderWindow(nextEncoderWindow(0, false), false)) == 2,
              "count consecutive bad ticks");
static_assert(encoderWindowBadCount(nextEncoderWindow(nextEncoderWindow(1, true), false)) == 2,
              "count separated anomalies within the same window");
static_assert(nextEncoderWindow(512, true) == 0, "old isolated anomaly expires after ten ticks");
