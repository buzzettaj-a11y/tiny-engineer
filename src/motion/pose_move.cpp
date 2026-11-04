#include "motion/pose_move.h"

#include <Arduino.h>

#include <cmath>

#include "animation.h"
#include "hardware/servo_wrapper.h"
#include "serial_log.h"
#include "servos.h"

namespace {

struct PoseMove {
  bool active = false;
  bool joint[SERVO_COUNT] = {};
  float from[SERVO_COUNT] = {};
  float to[SERVO_COUNT] = {};
  uint32_t startMs = 0;
  uint32_t durationMs = 0;
};

PoseMove g_move;

}  // namespace

void poseMoveStart(const PoseRequest& request, uint32_t nowMs) {
  PoseMove move;
  float maxDelta = 0.0f;

  for (int j = 0; j < SERVO_COUNT; j++) {
    if (!request.set[j]) {
      continue;
    }

    move.joint[j] = true;
    move.from[j] = servoAt(j).angle();
    // Pose space is the saved range; the soft limits clamp the result.
    move.to[j] = clampServoAngle(j, servoNormToDeg(j, request.norm[j]));
    maxDelta = fmaxf(maxDelta, fabsf(move.to[j] - move.from[j]));
  }

  move.startMs = nowMs;
  move.durationMs = poseEffectiveMs(request.ms, maxDelta, SERVO_MAX_SPEED_DEG_S);
  move.active = true;
  g_move = move;

  serialLogPrint("[pose] start ms=");
  serialLogPrint(move.durationMs);
  serialLogPrint(" max_delta=");
  serialLogPrintln(maxDelta, 1);
}

void poseMoveUpdate(uint32_t nowMs) {
  if (!g_move.active) {
    return;
  }

  if (getAnimation() != AnimationId::None || hasPendingAnimation()) {
    g_move.active = false;
    return;
  }

  // A tick may carry a timestamp taken just before the request started the move.
  const int32_t sinceStart = static_cast<int32_t>(nowMs - g_move.startMs);
  const uint32_t elapsed = sinceStart > 0 ? static_cast<uint32_t>(sinceStart) : 0;

  for (int j = 0; j < SERVO_COUNT; j++) {
    if (g_move.joint[j]) {
      servoAt(j).setTarget(
        poseInterpolate(g_move.from[j], g_move.to[j], elapsed, g_move.durationMs),
        SERVO_MAX_SPEED_DEG_S
      );
    }
  }

  if (elapsed >= g_move.durationMs) {
    g_move.active = false;
  }
}

void poseMoveCancel() {
  g_move.active = false;
}

bool poseMoveActive() {
  return g_move.active;
}
