#include "motion/servo_limits.h"

#include <cstdio>
#include <cstring>

namespace {

constexpr const char* kJointNames[SERVO_COUNT] = {"head", "neck", "left", "right", "body"};

float clampf(float v, float lo, float hi) {
  if (v < lo) {
    return lo;
  }
  if (v > hi) {
    return hi;
  }
  return v;
}

bool validJoint(int joint) {
  return joint >= 0 && joint < SERVO_COUNT;
}

}  // namespace

const char* bumpDirName(BumpDir dir) {
  return dir == BumpDir::Max ? "max" : "min";
}

bool parseBumpDir(const char* text, BumpDir& out) {
  if (text == nullptr) {
    return false;
  }
  if (strcmp(text, "min") == 0) {
    out = BumpDir::Min;
    return true;
  }
  if (strcmp(text, "max") == 0) {
    out = BumpDir::Max;
    return true;
  }
  return false;
}

void SoftLimits::reset(const float savedMins[SERVO_COUNT], const float savedMaxs[SERVO_COUNT]) {
  for (int i = 0; i < SERVO_COUNT; i++) {
    mins[i] = savedMins[i];
    maxs[i] = savedMaxs[i];
  }
}

float SoftLimits::clamp(int joint, float deg) const {
  if (!validJoint(joint)) {
    return deg;
  }
  return clampf(deg, mins[joint], maxs[joint]);
}

float SoftLimits::narrowAfterBump(int joint, float angle, BumpDir dir, float savedMid) {
  if (!validJoint(joint)) {
    return angle;
  }

  if (dir == BumpDir::Max) {
    float limit = angle - SERVO_BUMP_LIMIT_MARGIN_DEG;
    if (limit < savedMid) {
      limit = savedMid;
    }
    if (limit < maxs[joint]) {
      maxs[joint] = limit;
    }
    if (maxs[joint] < mins[joint]) {
      maxs[joint] = mins[joint];
    }
    return maxs[joint];
  }

  float limit = angle + SERVO_BUMP_LIMIT_MARGIN_DEG;
  if (limit > savedMid) {
    limit = savedMid;
  }
  if (limit > mins[joint]) {
    mins[joint] = limit;
  }
  if (mins[joint] > maxs[joint]) {
    mins[joint] = maxs[joint];
  }
  return mins[joint];
}

float bumpBackoffTarget(float angle, BumpDir dir, float savedMid) {
  if (dir == BumpDir::Max) {
    const float target = angle - SERVO_BUMP_BACKOFF_DEG;
    // Already on the near side of mid: still move away from the obstacle.
    if (angle <= savedMid) {
      return target;
    }
    return target < savedMid ? savedMid : target;
  }

  const float target = angle + SERVO_BUMP_BACKOFF_DEG;
  if (angle >= savedMid) {
    return target;
  }
  return target > savedMid ? savedMid : target;
}

const char* validateServoRanges(
  const float mins[SERVO_COUNT],
  const float maxs[SERVO_COUNT],
  const float hardMins[SERVO_COUNT],
  const float hardMaxs[SERVO_COUNT],
  float minSpanDeg,
  char* reason,
  size_t reasonSize
) {
  for (int i = 0; i < SERVO_COUNT; i++) {
    const char* name = kJointNames[i];

    if (mins[i] < hardMins[i]) {
      snprintf(reason, reasonSize, "%s min %g below hard min %g", name,
               static_cast<double>(mins[i]), static_cast<double>(hardMins[i]));
      return reason;
    }
    if (maxs[i] > hardMaxs[i]) {
      snprintf(reason, reasonSize, "%s max %g above hard max %g", name,
               static_cast<double>(maxs[i]), static_cast<double>(hardMaxs[i]));
      return reason;
    }
    if (!(mins[i] < maxs[i])) {
      snprintf(reason, reasonSize, "%s min must be below max", name);
      return reason;
    }
    if (maxs[i] - mins[i] < minSpanDeg) {
      snprintf(reason, reasonSize, "%s span below %g deg", name, static_cast<double>(minSpanDeg));
      return reason;
    }
  }

  return nullptr;
}
