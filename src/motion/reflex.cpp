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
#include "motion/explore_logic.h"
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
// Explore: longest wait for the holding-current baseline after the settle.
constexpr uint32_t kExploreArmTimeoutMs = 1000;
// Bump back-off and explore return moves.
constexpr float kBackoffSpeedDegS = anim::TRANSITION_TORSO_SPEED_DEG_S;
// Explore: move to the saved limit at this speed, then sweep at the asked speed.
constexpr float kExploreApproachSpeedDegS = 60.0f;
constexpr float kExploreReturnSpeedDegS = 30.0f;
constexpr uint32_t kExploreSettleMs = 300;
constexpr uint32_t kExploreStepMs = 10;

SoftLimits g_soft = {};
float g_syncedMins[SERVO_COUNT] = {};
float g_syncedMaxs[SERVO_COUNT] = {};
bool g_softSynced = false;

BumpDetector g_detector;
MotionTracker g_motion;
BumpLog g_bumps;
uint32_t g_lastReadMs = 0;
bool g_currentValid = false;
float g_currentMa = 0.0f;
bool g_sensorWasHealthy = true;
bool g_loggedHoldEvent = false;

void resetSoftFromSaved() {
  for (int i = 0; i < SERVO_COUNT; i++) {
    g_syncedMins[i] = settingsServoMin(i);
    g_syncedMaxs[i] = settingsServoMax(i);
  }

  g_soft.reset(g_syncedMins, g_syncedMaxs);
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
}

bool outputsWaking(uint32_t nowMs) {
  // Signed: the wake stamp may be newer than a tick's `now`.
  return static_cast<int32_t>(nowMs - servoOutputsLastWakeMs()) <
         static_cast<int32_t>(kOutputWakeIgnoreMs);
}

bool readCurrent(float& outMa) {
  if (!ina219ReadCurrentMa(outMa)) {
    if (!ina219Healthy()) {
      g_currentValid = false;
    }
    return false;
  }

  g_currentMa = outMa;
  g_currentValid = true;
  return true;
}

void currentAngles(float out[SERVO_COUNT]) {
  for (int j = 0; j < SERVO_COUNT; j++) {
    out[j] = servoAt(j).angle();
  }
}

void observeMotion(uint32_t nowMs) {
  float angles[SERVO_COUNT];
  float targets[SERVO_COUNT];

  for (int j = 0; j < SERVO_COUNT; j++) {
    angles[j] = servoAt(j).angle();
    targets[j] = servoAt(j).target();
  }

  g_motion.observe(nowMs, angles, targets);

  // A /pose move re-targets every tick by fractions of a degree; count it whole.
  if (poseMoveActive()) {
    g_motion.noteMotion(nowMs);
  }
}

// The reflex's own stop and back-off are not "motion" anyone can be blamed for.
void claimReflexTargets() {
  float targets[SERVO_COUNT];

  for (int j = 0; j < SERVO_COUNT; j++) {
    targets[j] = servoAt(j).target();
  }

  g_motion.claim(targets);
}

void onEvent(BumpEvent event, uint32_t nowMs) {
  const bool stall = event == BumpEvent::Stall;

  struct Hit {
    int joint;
    float angle;
    BumpDir dir;
  };

  Hit hits[SERVO_COUNT];
  size_t hitCount = 0;

  // Commanded angle when the over-threshold window opened (near first contact),
  // not the one now, which has run further into the obstacle.
  auto contactAngle = [](int j) {
    return g_detector.eventAnglesValid() ? g_detector.eventAngle(j) : servoAt(j).angle();
  };

  if (stall) {
    // Only a single joint whose own motion just ended, pressing on the limit on
    // its side of mid. Anything less certain: let go instead of narrowing.
    const int j = stallBlameJoint(g_motion, nowMs);
    BumpDir dir = BumpDir::Max;

    if (j >= 0 && stallDir(contactAngle(j), servoRuntimeMid(j), dir)) {
      hits[hitCount++] = {j, contactAngle(j), dir};
    }
  } else {
    for (int j = 0; j < SERVO_COUNT; j++) {
      if (g_motion.jointRecent(j, nowMs, BUMP_MOTION_TAIL_MS)) {
        hits[hitCount++] = {j, contactAngle(j), g_motion.jointDir(j)};
      }
    }
  }

  const float peakMa = g_detector.eventPeakMa();

  if (hitCount == 0 && servoOutputsHoldActive()) {
    // /servos/park holds PWM until reboot, so releasing is a no-op; leave the
    // pose alone and say so once instead of firing every cooldown.
    if (!g_loggedHoldEvent) {
      g_loggedHoldEvent = true;
      serialLogPrint("[bump] high current during park hold; ma=");
      serialLogPrint(peakMa, 0);
      serialLogPrintln("; outputs held, ignoring");
    }
    return;
  }

  // Copy before the switch to none: the log names what was running.
  char animName[BUMP_ANIM_NAME_LEN + 1];
  snprintf(animName, sizeof(animName), "%s", animationName(getAnimation()));

  poseMoveCancel();
  setAnimationImmediately(AnimationId::None);
  // none parks every joint; a bump freezes them where they are instead.
  anim::stopAnimServos();

  if (hitCount == 0) {
    // High current, blame uncertain: let go instead of pushing on.
    serialLogPrint(stall ? "[bump] stall" : "[bump] bump");
    serialLogPrint(" without a joint to blame; ma=");
    serialLogPrint(peakMa, 0);
    serialLogPrintln("; releasing servo outputs");
    releaseAllServoOutputs();
    claimReflexTargets();
    return;
  }

  g_loggedHoldEvent = false;

  for (size_t i = 0; i < hitCount; i++) {
    const Hit& hit = hits[i];
    const float mid = servoRuntimeMid(hit.joint);
    const float limit = g_soft.narrowAfterBump(hit.joint, hit.angle, hit.dir, mid);
    const float backoff = g_soft.clamp(hit.joint, bumpBackoffTarget(hit.angle, hit.dir, mid));

    servoAt(hit.joint).setTarget(backoff, kBackoffSpeedDegS);
    g_bumps.push(BumpLog::make(nowMs, hit.joint, hit.angle, hit.dir, peakMa, animName, stall));

    serialLogPrint(stall ? "[bump] stall joint=" : "[bump] joint=");
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

  claimReflexTargets();
}

// Explore helpers -----------------------------------------------------------

enum class SweepEnd {
  Reached,       // got to the target
  Bump,          // detector fired
  HardStop,      // the hard cap held the joint short of the target
  Timeout,       // EXPLORE_TIME_LIMIT_MS ran out
  SensorFailed,  // EXPLORE_MAX_READ_FAILURES reads in a row failed
};

struct ExploreRun {
  BumpDetector detector;
  uint16_t thresholdMa = EXPLORE_THRESHOLD_CAP_MA;
  float peakMa = 0.0f;
  uint8_t readFailures = 0;
  bool sensorFailed = false;
  uint32_t deadlineMs = 0;

  // One current sample. True when it completes a bump.
  bool sample(bool moving) {
    float ma = 0.0f;

    if (!readCurrent(ma)) {
      if (++readFailures >= EXPLORE_MAX_READ_FAILURES) {
        sensorFailed = true;
      }
      return false;
    }

    readFailures = 0;

    if (moving && ma > peakMa) {
      peakMa = ma;
    }

    float angles[SERVO_COUNT];
    currentAngles(angles);
    const BumpSample s{static_cast<uint32_t>(millis()), ma, moving, true, thresholdMa, angles};
    // Still samples only learn the holding current here.
    return detector.update(s) == BumpEvent::Bump && moving;
  }

  bool timedOut() const {
    return static_cast<int32_t>(millis() - deadlineMs) >= 0;
  }

  // Waits out a servo-output wake (that spike stays out of the baseline), then
  // holds still for `ms` of samples and until the holding-current baseline is
  // armed, at most kExploreArmTimeoutMs. Callers check armed() afterwards.
  void settle(uint32_t ms) {
    while (outputsWaking(millis()) && !timedOut()) {
      float ma = 0.0f;
      readCurrent(ma);
      delay(kExploreStepMs);
    }

    const uint32_t start = millis();

    while (!sensorFailed && !timedOut()) {
      const uint32_t elapsed = millis() - start;

      if (elapsed >= kExploreArmTimeoutMs ||
          (elapsed >= ms && detector.armed(true))) {
        break;
      }

      sample(false);
      delay(kExploreStepMs);
    }

    // A stall while holding still must not blind the next sweep's first 500 ms.
    detector.clearCooldown();
  }

  // Drives `joint` to `target` at `speedDegS` (hard-clamped writes).
  // `watch`: stop on a bump, a sensor failure or the time limit.
  SweepEnd sweep(int joint, float target, float speedDegS, bool watch) {
    ServoWrapper& servo = servoAt(joint);
    float pos = servo.angle();
    uint32_t last = millis();

    while (fabsf(target - pos) >= SERVO_ANGLE_DEADBAND_DEG) {
      if (watch && timedOut()) {
        return SweepEnd::Timeout;
      }

      delay(kExploreStepMs);
      const uint32_t now = millis();
      const float maxStep = speedDegS * static_cast<float>(now - last) / 1000.0f;
      last = now;

      const float delta = target - pos;
      const float commanded = pos + (delta > 0.0f ? fminf(delta, maxStep) : fmaxf(delta, -maxStep));
      servo.driveHardClamped(commanded);
      pos = servo.angle();

      const bool bumped = sample(true);

      if (watch && bumped) {
        return SweepEnd::Bump;
      }

      if (watch && sensorFailed) {
        return SweepEnd::SensorFailed;
      }

      // The hard cap held the joint short of an out-of-range target.
      if (fabsf(pos - commanded) > 0.01f) {
        return SweepEnd::HardStop;
      }
    }

    servo.driveHardClamped(target);
    return SweepEnd::Reached;
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
  // Motion history runs with or without the sensor (cheap, and it keeps the
  // tail correct the moment a sensor comes back).
  observeMotion(nowMs);

  if (!ina219Present() || nowMs - g_lastReadMs < kCurrentPollMs) {
    return;
  }

  g_lastReadMs = nowMs;
  float ma = 0.0f;
  const bool read = readCurrent(ma);
  const bool healthy = ina219Healthy();

  if (healthy != g_sensorWasHealthy) {
    g_sensorWasHealthy = healthy;
    // Baselines from before a sensor loss are not trusted afterwards.
    g_detector.reset();
  }

  if (!read) {
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

  float angles[SERVO_COUNT];
  currentAngles(angles);

  const BumpSample sample{
    nowMs,
    ma,
    g_motion.active(nowMs),
    !servoOutputsReleased(),
    settingsBumpThresholdMa(),
    angles,
  };

  const BumpEvent event = g_detector.update(sample);

  if (event != BumpEvent::None) {
    onEvent(event, nowMs);
  }
}

bool motionHasCurrentSensor() {
  return ina219Healthy();
}

bool motionCurrentMa(float& outMa) {
  if (!ina219Healthy() || !g_currentValid) {
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

  if (!ina219Healthy()) {
    result.code = 409;
    result.error = "current sensor not responding";
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
  const ExploreBound bound =
    exploreSweepTarget(savedLimit, hardLimit, dir, EXPLORE_MAX_EXCURSION_DEG);

  serialLogPrint("[explore] joint=");
  serialLogPrint(joint);
  serialLogPrint(" dir=");
  serialLogPrint(bumpDirName(dir));
  serialLogPrint(" from=");
  serialLogPrint(savedLimit, 1);
  serialLogPrint(" to=");
  serialLogPrint(bound.target, 1);
  serialLogPrint(" speed=");
  serialLogPrintln(speedDegS, 1);

  // Stop everything; every joint holds (PWM on) where it is.
  poseMoveCancel();
  setAnimationImmediately(AnimationId::None);
  anim::stopAnimServos();
  ensureAllServoOutputs();
  activitySetExploring(true);

  ExploreRun run;
  run.thresholdMa = exploreThresholdMa(settingsBumpThresholdMa());
  run.deadlineMs = millis() + EXPLORE_TIME_LIMIT_MS;

  ServoWrapper& servo = servoAt(joint);
  const float startAngle = servo.angle();

  run.settle(kExploreSettleMs);

  if (run.sensorFailed) {
    activitySetExploring(false);
    result.code = 500;
    result.error = "current sensor failed during explore";
    return result;
  }

  if (!run.detector.armed(true)) {
    // Not enough good still samples within kExploreArmTimeoutMs (flaky reads).
    activitySetExploring(false);
    serialLogPrintln("[explore] could not learn the holding current");
    result.code = 500;
    result.error = "could not read a steady servo current";
    return result;
  }

  // Holding current of powered servos is always positive; a tiny or negative
  // baseline means the shunt is not in the servo lead the right way round.
  if (!exploreBaselinePlausible(run.detector.baselineMa(true))) {
    activitySetExploring(false);
    serialLogPrint("[explore] implausible holding current ");
    serialLogPrintln(run.detector.baselineMa(true), 1);
    result.code = 409;
    result.error = "implausible servo current; check INA219 VIN+/VIN- wiring";
    return result;
  }

  SweepEnd end = run.sweep(joint, savedLimit, kExploreApproachSpeedDegS, true);

  if (end == SweepEnd::Reached) {
    run.settle(kExploreSettleMs);
    end = run.sensorFailed ? SweepEnd::SensorFailed
                           : run.sweep(joint, bound.target, speedDegS, true);
  }

  result.stoppedAt = servo.angle();
  result.peakMa = run.peakMa;

  // Back off from first contact when there was one, else from where it stopped.
  const float contact = end == SweepEnd::Bump && run.detector.eventAnglesValid()
                          ? run.detector.eventAngle(joint)
                          : result.stoppedAt;
  result.safe = constrain(
    bumpBackoffTarget(contact, dir, mid),
    SERVO_HARD_MIN_DEG[joint],
    SERVO_HARD_MAX_DEG[joint]
  );

  if (end == SweepEnd::SensorFailed) {
    // Blind now: go straight back to where the joint started.
    run.sweep(joint, g_soft.clamp(joint, startAngle), kExploreReturnSpeedDegS, false);
    activitySetExploring(false);
    noteActivity(millis());
    serialLogPrintln("[explore] current reads failed; aborted");
    result.code = 500;
    result.error = "current sensor failed during explore";
    return result;
  }

  switch (end) {
    case SweepEnd::Bump:
      result.reason = "bump";
      break;
    case SweepEnd::Timeout:
      result.reason = "max_travel";
      break;
    case SweepEnd::Reached:
      result.reason = bound.excursionCapped ? "max_travel" : "hard_limit";
      break;
    default:
      result.reason = "hard_limit";
      break;
  }

  run.sweep(joint, result.safe, kExploreReturnSpeedDegS, false);
  // Leave the joint where animations may command it, so the next move does not jump.
  run.sweep(joint, g_soft.clamp(joint, result.safe), kExploreReturnSpeedDegS, false);

  activitySetExploring(false);
  noteActivity(millis());
  result.code = 200;

  serialLogPrint("[explore] stopped_at=");
  serialLogPrint(result.stoppedAt, 1);
  serialLogPrint(" reason=");
  serialLogPrint(result.reason);
  serialLogPrint(" safe=");
  serialLogPrint(result.safe, 1);
  serialLogPrint(" peak_ma=");
  serialLogPrintln(result.peakMa, 0);
  return result;
}
