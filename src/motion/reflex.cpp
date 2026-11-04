#include "motion/reflex.h"

#include <Arduino.h>

#include <cmath>
#include <cstdio>

#include "animation.h"
#include "animation/constants.h"
#include "animation/util.h"
#include "hardware/activity.h"
#include "hardware/ina219.h"
#include "hardware/servo_wrapper.h"
#include "motion/pose_move.h"
#include "network/wifi_connect.h"
#include "serial_log.h"
#include "servos.h"
#include "settings/settings.h"
#include "sleep.h"

namespace {

// INA219 conversions take 4.26 ms (8x averaged); reading faster gains nothing.
constexpr uint32_t kCurrentPollMs = 5;
// Skip detection this long after a released servo output switches back on:
// a joint that drooped while limp snaps back with a short current spike.
constexpr uint32_t kOutputWakeIgnoreMs = 400;
// Bump back-off and explore return moves.
constexpr float kBackoffSpeedDegS = anim::TRANSITION_TORSO_SPEED_DEG_S;
// Explore: move to the saved limit at this speed, then sweep at the asked speed.
constexpr float kExploreApproachSpeedDegS = 40.0f;
constexpr float kExploreReturnSpeedDegS = 20.0f;
constexpr uint32_t kExploreSettleMs = 300;
constexpr uint32_t kExploreStepMs = 10;

SoftLimits g_soft = {};
float g_syncedMins[SERVO_COUNT] = {};
float g_syncedMaxs[SERVO_COUNT] = {};
bool g_softSynced = false;

BumpDetector g_detector;
BumpLog g_bumps;
uint32_t g_lastReadMs = 0;
bool g_currentValid = false;
float g_currentMa = 0.0f;

void resetSoftFromSaved() {
  for (int i = 0; i < SERVO_COUNT; i++) {
    g_syncedMins[i] = settingsServoMin(i);
    g_syncedMaxs[i] = settingsServoMax(i);
  }

  g_soft.reset(g_syncedMins, g_syncedMaxs, SERVO_HARD_MIN_DEG, SERVO_HARD_MAX_DEG);
  g_softSynced = true;
}

void syncSoftLimits() {
  if (g_softSynced) {
    bool same = true;

    for (int i = 0; i < SERVO_COUNT && same; i++) {
      same = g_syncedMins[i] == settingsServoMin(i) && g_syncedMaxs[i] == settingsServoMax(i);
    }

    if (same) {
      return;
    }
  }

  resetSoftFromSaved();

  for (int i = 0; i < SERVO_COUNT; i++) {
    if (g_soft.mins[i] != g_syncedMins[i] || g_soft.maxs[i] != g_syncedMaxs[i]) {
      serialLogPrint("[servo] saved range of joint ");
      serialLogPrint(i);
      serialLogPrintln(" exceeds the hard caps; clamped");
    }
  }
}

bool outputsWaking(uint32_t nowMs) {
  // Signed: the wake stamp may be newer than a tick's `now`.
  return static_cast<int32_t>(nowMs - servoOutputsLastWakeMs()) <
         static_cast<int32_t>(kOutputWakeIgnoreMs);
}

bool readCurrent(float& outMa) {
  if (!ina219ReadCurrentMa(outMa)) {
    return false;
  }

  g_currentMa = outMa;
  g_currentValid = true;
  return true;
}

void onBump(uint32_t nowMs) {
  struct Hit {
    int joint;
    float angle;
    BumpDir dir;
  };

  Hit hits[SERVO_COUNT];
  size_t hitCount = 0;

  for (int j = 0; j < SERVO_COUNT; j++) {
    const ServoWrapper& servo = servoAt(j);

    if (servo.isMoving()) {
      hits[hitCount++] = {j, servo.angle(), travelDir(servo.angle(), servo.target())};
    }
  }

  if (hitCount == 0) {
    return;
  }

  // Copy before the switch to none: the log names what was running.
  char animName[BUMP_ANIM_NAME_LEN + 1];
  snprintf(animName, sizeof(animName), "%s", animationName(getAnimation()));
  const float peakMa = g_detector.bumpPeakMa();

  poseMoveCancel();
  setAnimationImmediately(AnimationId::None);
  // none parks every joint; a bump freezes them where they are instead.
  anim::stopAnimServos();

  for (size_t i = 0; i < hitCount; i++) {
    const Hit& hit = hits[i];
    const float mid = servoRuntimeMid(hit.joint);
    const float limit = g_soft.narrowAfterBump(hit.joint, hit.angle, hit.dir, mid);
    const float backoff = g_soft.clamp(hit.joint, bumpBackoffTarget(hit.angle, hit.dir, mid));

    servoAt(hit.joint).setTarget(backoff, kBackoffSpeedDegS);
    g_bumps.push(BumpLog::make(nowMs, hit.joint, hit.angle, hit.dir, peakMa, animName));

    serialLogPrint("[bump] joint=");
    serialLogPrint(hit.joint);
    serialLogPrint(" angle=");
    serialLogPrint(hit.angle, 1);
    serialLogPrint(" dir=");
    serialLogPrint(bumpDirName(hit.dir));
    serialLogPrint(" ma=");
    serialLogPrint(peakMa, 0);
    serialLogPrint(" soft_limit=");
    serialLogPrint(limit, 1);
    serialLogPrint(" anim=");
    serialLogPrintln(animName);
  }
}

// Explore helpers -----------------------------------------------------------

struct ExploreRun {
  BumpDetector detector;
  uint16_t thresholdMa;
  float peakMa = 0.0f;

  // One current sample. True when it completes a bump.
  bool sample(bool moving) {
    float ma = 0.0f;

    if (!readCurrent(ma)) {
      return false;
    }

    if (moving && ma > peakMa) {
      peakMa = ma;
    }

    return detector.update(millis(), ma, moving, thresholdMa);
  }

  // Holds still for `ms` while the baseline learns the holding current
  // (longer if outputs just woke: that spike stays out of the baseline).
  void settle(uint32_t ms) {
    const uint32_t start = millis();

    while (millis() - start < ms || outputsWaking(millis())) {
      if (outputsWaking(millis())) {
        float ma = 0.0f;
        readCurrent(ma);
      } else {
        sample(false);
      }
      delay(kExploreStepMs);
    }
  }

  // Drives `joint` to `target` at `speedDegS` (hard-clamped writes).
  // `watch` = stop on a bump; returns true when one fired.
  bool sweep(int joint, float target, float speedDegS, bool watch) {
    ServoWrapper& servo = servoAt(joint);
    float pos = servo.angle();
    uint32_t last = millis();

    while (fabsf(target - pos) >= SERVO_ANGLE_DEADBAND_DEG) {
      delay(kExploreStepMs);
      const uint32_t now = millis();
      const float maxStep = speedDegS * static_cast<float>(now - last) / 1000.0f;
      last = now;

      const float delta = target - pos;
      const float commanded = pos + (delta > 0.0f ? fminf(delta, maxStep) : fmaxf(delta, -maxStep));
      servo.driveHardClamped(commanded);
      pos = servo.angle();

      if (sample(true) && watch) {
        return true;
      }

      // The hard cap held the joint short of an out-of-range target.
      if (fabsf(pos - commanded) > 0.01f) {
        return false;
      }
    }

    servo.driveHardClamped(target);
    return false;
  }
};

}  // namespace

float servoSoftMin(int joint) {
  syncSoftLimits();
  return joint >= 0 && joint < SERVO_COUNT ? g_soft.mins[joint] : 0.0f;
}

float servoSoftMax(int joint) {
  syncSoftLimits();
  return joint >= 0 && joint < SERVO_COUNT ? g_soft.maxs[joint] : 180.0f;
}

float servoSoftClamp(int joint, float deg) {
  syncSoftLimits();
  return g_soft.clamp(joint, deg);
}

void servoSoftLimitsReset() {
  resetSoftFromSaved();
}

void motionTick(uint32_t nowMs) {
  if (!ina219Present() || nowMs - g_lastReadMs < kCurrentPollMs) {
    return;
  }

  g_lastReadMs = nowMs;
  float ma = 0.0f;

  if (!readCurrent(ma)) {
    return;
  }

  // Setup AP moves bypass the limits on purpose; no reflex there.
  if (wifiProvisioningMode()) {
    return;
  }

  syncSoftLimits();

  if (outputsWaking(nowMs)) {
    return;
  }

  if (g_detector.update(nowMs, ma, anyServoMoving(), settingsBumpThresholdMa())) {
    onBump(nowMs);
  }
}

bool motionHasCurrentSensor() {
  return ina219Present();
}

bool motionCurrentMa(float& outMa) {
  if (!ina219Present() || !g_currentValid) {
    return false;
  }

  outMa = g_currentMa;
  return true;
}

const BumpLog& motionBumps() {
  return g_bumps;
}

void motionClearBumps() {
  g_bumps.clear();
  resetSoftFromSaved();
  serialLogPrintln("[bump] cleared; soft limits = saved");
}

const char* motionSaveRanges(
  const uint8_t mins[SERVO_COUNT],
  const uint8_t maxs[SERVO_COUNT],
  char* reason,
  size_t reasonSize
) {
  float minDeg[SERVO_COUNT];
  float maxDeg[SERVO_COUNT];

  for (int i = 0; i < SERVO_COUNT; i++) {
    minDeg[i] = mins[i];
    maxDeg[i] = maxs[i];
  }

  if (validateServoRanges(
        minDeg,
        maxDeg,
        SERVO_HARD_MIN_DEG,
        SERVO_HARD_MAX_DEG,
        SERVO_RANGE_MIN_SPAN_DEG,
        reason,
        reasonSize
      ) != nullptr) {
    return reason;
  }

  ServoRanges ranges{};

  for (int i = 0; i < SERVO_COUNT; i++) {
    ranges.mins[i] = mins[i];
    ranges.maxs[i] = maxs[i];
  }

  SettingsPatch patch;
  patch.servoRanges = ranges;

  if (!saveSettings(patch)) {
    snprintf(reason, reasonSize, "save failed");
    return reason;
  }

  resetSoftFromSaved();
  serialLogPrintln("[servo] ranges saved via POST /ranges; soft limits = saved");
  return nullptr;
}

ExploreResult motionExplore(int joint, BumpDir dir, float speedDegS) {
  ExploreResult result{};
  result.joint = joint;
  result.dir = dir;

  if (!ina219Present()) {
    result.code = 409;
    result.error = "no current sensor";
    return result;
  }

  syncSoftLimits();

  const float mid = servoRuntimeMid(joint);
  const float hardLimit = dir == BumpDir::Max ? SERVO_HARD_MAX_DEG[joint] : SERVO_HARD_MIN_DEG[joint];
  const float savedLimit = constrain(
    dir == BumpDir::Max ? settingsServoMax(joint) : settingsServoMin(joint),
    SERVO_HARD_MIN_DEG[joint],
    SERVO_HARD_MAX_DEG[joint]
  );

  serialLogPrint("[explore] joint=");
  serialLogPrint(joint);
  serialLogPrint(" dir=");
  serialLogPrint(bumpDirName(dir));
  serialLogPrint(" from=");
  serialLogPrint(savedLimit, 1);
  serialLogPrint(" to=");
  serialLogPrint(hardLimit, 1);
  serialLogPrint(" speed=");
  serialLogPrintln(speedDegS, 1);

  // Stop everything; every joint holds (PWM on) where it is.
  poseMoveCancel();
  setAnimationImmediately(AnimationId::None);
  anim::stopAnimServos();
  ensureAllServoOutputs();
  activitySetExploring(true);

  ExploreRun run;
  run.thresholdMa = settingsBumpThresholdMa() > 0 ? settingsBumpThresholdMa()
                                                  : SETTINGS_DEFAULT_BUMP_THRESHOLD_MA;

  run.settle(kExploreSettleMs);
  bool bumped = run.sweep(joint, savedLimit, kExploreApproachSpeedDegS, true);

  if (!bumped) {
    run.settle(kExploreSettleMs);
    bumped = run.sweep(joint, hardLimit, speedDegS, true);
  }

  ServoWrapper& servo = servoAt(joint);
  result.stoppedAt = servo.angle();
  result.bumped = bumped;
  result.peakMa = run.peakMa;
  result.safe = constrain(
    bumpBackoffTarget(result.stoppedAt, dir, mid),
    SERVO_HARD_MIN_DEG[joint],
    SERVO_HARD_MAX_DEG[joint]
  );

  run.sweep(joint, result.safe, kExploreReturnSpeedDegS, false);
  // Leave the joint where animations may command it, so the next move does not jump.
  run.sweep(joint, g_soft.clamp(joint, result.safe), kExploreReturnSpeedDegS, false);

  activitySetExploring(false);
  noteActivity(millis());
  result.code = 200;

  serialLogPrint("[explore] stopped_at=");
  serialLogPrint(result.stoppedAt, 1);
  serialLogPrint(bumped ? " reason=bump" : " reason=hard_limit");
  serialLogPrint(" safe=");
  serialLogPrint(result.safe, 1);
  serialLogPrint(" peak_ma=");
  serialLogPrintln(result.peakMa, 0);
  return result;
}
