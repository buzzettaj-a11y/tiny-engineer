#pragma once

#include <cstddef>
#include <cstdint>

#include "servos.h"

// Runtime servo limits for the bump reflex (chip/PROTOCOL.md sections 4–5).
// Pure logic, no Arduino dependencies (host-tested).
//
// saved: NVS calibration; animations map poses -1..1 onto it.
// soft:  RAM-only limits that bumps narrow; animations and /pose clamp to them.
// hard:  compile-time caps in servos.h; nothing exceeds them.

enum class BumpDir : uint8_t {
  Min,
  Max,
};

const char* bumpDirName(BumpDir dir);
// "min" / "max"; false for anything else (nullptr included).
bool parseBumpDir(const char* text, BumpDir& out);

// A bump moves the joint back this far toward its saved mid.
constexpr float SERVO_BUMP_BACKOFF_DEG = 4.0f;
// The soft limit lands this far inside the angle where the bump happened.
constexpr float SERVO_BUMP_LIMIT_MARGIN_DEG = 2.0f;

struct SoftLimits {
  float mins[SERVO_COUNT];
  float maxs[SERVO_COUNT];

  // Soft = saved clamped into hard. A saved range wholly outside the hard caps
  // collapses to the hard cap nearest to it.
  void reset(
    const float savedMins[SERVO_COUNT],
    const float savedMaxs[SERVO_COUNT],
    const float hardMins[SERVO_COUNT],
    const float hardMaxs[SERVO_COUNT]
  );

  float clamp(int joint, float deg) const;

  // Narrows the limit on the side the joint was travelling to
  // angle -/+ SERVO_BUMP_LIMIT_MARGIN_DEG, never past savedMid and never wider
  // than it already is. Returns the new limit on that side.
  float narrowAfterBump(int joint, float angle, BumpDir dir, float savedMid);
};

// Where a bumped joint backs off to: SERVO_BUMP_BACKOFF_DEG against the travel
// direction. Outward moves (away from savedMid) stop at savedMid when closer than
// that; a joint bumped on its way back toward mid still retreats from the
// obstacle. Callers clamp the result to the soft limits.
float bumpBackoffTarget(float angle, BumpDir dir, float savedMid);

// Direction a joint travels from `angle` to `target`.
inline BumpDir travelDir(float angle, float target) {
  return target >= angle ? BumpDir::Max : BumpDir::Min;
}

// POST /ranges validation: hard_min <= min < max <= hard_max and
// max - min >= minSpanDeg for each joint. Returns nullptr when valid, else a
// reason written into `reason` (also returned).
const char* validateServoRanges(
  const float mins[SERVO_COUNT],
  const float maxs[SERVO_COUNT],
  const float hardMins[SERVO_COUNT],
  const float hardMaxs[SERVO_COUNT],
  float minSpanDeg,
  char* reason,
  size_t reasonSize
);
