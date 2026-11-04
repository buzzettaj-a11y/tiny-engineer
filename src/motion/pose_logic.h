#pragma once

#include <cstdint>

#include "servos.h"

// POST /pose math (chip/PROTOCOL.md section 4). Pure, host-tested.

constexpr uint32_t POSE_MS_MIN = 100;
constexpr uint32_t POSE_MS_MAX = 5000;
constexpr uint32_t POSE_MS_DEFAULT = 600;

// One pose request: per joint, `set` false = leave that joint (JSON null).
struct PoseRequest {
  bool set[SERVO_COUNT];
  float norm[SERVO_COUNT];  // -1..1 in the saved range
  uint32_t ms;
};

bool poseNormValid(float n);
bool poseMsValid(uint32_t ms);

// The eased move peaks at 3x its average speed (cubic in-out), so the move is
// stretched until that peak stays under maxSpeedDegS. Returns at least requestedMs.
uint32_t poseEffectiveMs(uint32_t requestedMs, float maxDeltaDeg, float maxSpeedDegS);

// Eased (cubic in-out) angle `elapsedMs` into a move of `durationMs`.
float poseInterpolate(float fromDeg, float toDeg, uint32_t elapsedMs, uint32_t durationMs);
