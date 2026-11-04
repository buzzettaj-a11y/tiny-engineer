#include "motion/pose_logic.h"

#include <cmath>

namespace {

// Peak / average speed of the cubic in-out ease (derivative at t = 0.5).
constexpr float kEasePeakFactor = 3.0f;

float easeInOutCubic(float t) {
  if (t <= 0.0f) {
    return 0.0f;
  }
  if (t >= 1.0f) {
    return 1.0f;
  }
  if (t < 0.5f) {
    return 4.0f * t * t * t;
  }
  const float f = -2.0f * t + 2.0f;
  return 1.0f - (f * f * f) / 2.0f;
}

}  // namespace

bool poseNormValid(float n) {
  return std::isfinite(n) && n >= -1.0f && n <= 1.0f;
}

bool poseMsValid(uint32_t ms) {
  return ms >= POSE_MS_MIN && ms <= POSE_MS_MAX;
}

uint32_t poseEffectiveMs(uint32_t requestedMs, float maxDeltaDeg, float maxSpeedDegS) {
  if (maxSpeedDegS <= 0.0f || maxDeltaDeg <= 0.0f) {
    return requestedMs;
  }

  const float minMs = std::ceil(kEasePeakFactor * maxDeltaDeg / maxSpeedDegS * 1000.0f);
  const uint32_t floorMs = static_cast<uint32_t>(minMs);
  return floorMs > requestedMs ? floorMs : requestedMs;
}

float poseInterpolate(float fromDeg, float toDeg, uint32_t elapsedMs, uint32_t durationMs) {
  if (durationMs == 0 || elapsedMs >= durationMs) {
    return toDeg;
  }

  const float t = static_cast<float>(elapsedMs) / static_cast<float>(durationMs);
  return fromDeg + (toDeg - fromDeg) * easeInOutCubic(t);
}
