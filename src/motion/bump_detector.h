#pragma once

#include <cstddef>
#include <cstdint>

#include "motion/servo_limits.h"
#include "servos.h"

// Current-based bump detection (chip/PROTOCOL.md section 5).
// Pure logic, no Arduino dependencies (host-tested).

// Over threshold for at least this long while moving = bump.
constexpr uint32_t BUMP_HOLD_MS = 60;
// Bumps and stalls are ignored this long after one fires.
constexpr uint32_t BUMP_COOLDOWN_MS = 500;
// "Moving" lasts this long after the last commanded motion (target change,
// joint still slewing, /pose or explore). Servos keep pushing after the
// commanded angle arrives, and slow /pose moves re-target every tick without
// ever being more than a deadband from their target.
constexpr uint32_t BUMP_MOTION_TAIL_MS = 250;
// Not moving, current over baseline + threshold this long = static stall
// (a joint pressing into something after its move ended).
constexpr uint32_t BUMP_STALL_MS = 300;
// Still, between baseline + threshold/2 and baseline + threshold: a new holding
// load is only learned into the baseline after this long.
constexpr uint32_t BUMP_BASELINE_SLOW_ACCEPT_MS = 2000;
// Still samples (per output state) before a baseline counts and the reflex arms.
constexpr uint8_t BUMP_ARM_STILL_SAMPLES = 10;
// Any sample at or above this trips at once (moving or still), when enabled.
constexpr float BUMP_ABSOLUTE_TRIP_MA = 2500.0f;
// Idle baseline EMA weight per still sample (~0.3 s time constant at 10–20 ms reads).
constexpr float BUMP_BASELINE_ALPHA = 0.05f;

enum class BumpEvent : uint8_t {
  None,
  Bump,   // over threshold for BUMP_HOLD_MS while moving (or the absolute trip)
  Stall,  // over threshold for BUMP_STALL_MS with no motion
};

struct BumpSample {
  uint32_t nowMs;
  float currentMa;
  // Motion commanded within BUMP_MOTION_TAIL_MS (MotionTracker::active()).
  bool motion;
  // At least one PCA9685 output energised. Limp and powered servos draw very
  // different idle currents, so each state keeps its own baseline.
  bool outputsOn;
  // 0 disables detection; the baselines still learn.
  uint16_t thresholdMa;
  // Optional (SERVO_COUNT entries): commanded joint angles at this sample.
  // Snapshotted when an over-threshold window opens, i.e. near first contact.
  const float* angles;
};

class BumpDetector {
public:
  BumpEvent update(const BumpSample& sample);
  void reset();
  // Ends a running cooldown (explore: a stall while settling must not blind
  // the sweep that follows).
  void clearCooldown() { cooling_ = false; }

  // A baseline for the given output state exists (enough still samples).
  bool armed(bool outputsOn) const;
  float baselineMa(bool outputsOn) const;

  // Details of the last event.
  float eventPeakMa() const { return eventPeakMa_; }
  bool eventAnglesValid() const { return eventAnglesValid_; }
  float eventAngle(int joint) const;

private:
  struct Baseline {
    bool valid = false;
    float ma = 0.0f;
    uint8_t stillSamples = 0;
  };

  Baseline& baselineFor(bool outputsOn) { return outputsOn ? on_ : off_; }
  void openWindow(const BumpSample& sample);
  BumpEvent fire(BumpEvent event, const BumpSample& sample);
  void learn(Baseline& baseline, float ma);
  void closeWindows();

  Baseline on_;
  Baseline off_;
  bool lastOutputsOn_ = false;
  bool seenSample_ = false;

  // Over-threshold window (moving or still).
  bool windowOpen_ = false;
  bool windowMoving_ = false;
  uint32_t windowSinceMs_ = 0;
  float windowPeakMa_ = 0.0f;
  bool windowAnglesValid_ = false;
  float windowAngles_[SERVO_COUNT] = {};

  // Still, between half and full threshold.
  bool midOpen_ = false;
  uint32_t midSinceMs_ = 0;

  bool cooling_ = false;
  uint32_t lastEventMs_ = 0;
  float eventPeakMa_ = 0.0f;
  bool eventAnglesValid_ = false;
  float eventAngles_[SERVO_COUNT] = {};
};

// Who moved, and when: commanded motion per joint, with the tail.
class MotionTracker {
public:
  // Once per tick with each joint's commanded angle and target. A joint counts
  // as moving while it is more than SERVO_ANGLE_DEADBAND_DEG from its target
  // or when its target changed since the previous call.
  void observe(uint32_t nowMs, const float angles[SERVO_COUNT], const float targets[SERVO_COUNT]);
  // Motion that is not visible per joint (a /pose move in flight, explore).
  void noteMotion(uint32_t nowMs);
  // The reflex owns these targets (its own stop and back-off). While a joint's
  // target stays at the claimed value its motion still counts as "moving" but is
  // never blamed and never sets the joint's direction. Any other target ends it.
  void claim(const float targets[SERVO_COUNT]);

  // Any motion within BUMP_MOTION_TAIL_MS.
  bool active(uint32_t nowMs) const;
  // Joint moved within `windowMs`.
  bool jointRecent(int joint, uint32_t nowMs, uint32_t windowMs) const;
  // Direction of the joint's last commanded motion.
  BumpDir jointDir(int joint) const;

private:
  bool seen_ = false;
  float lastTargets_[SERVO_COUNT] = {};
  bool claimed_[SERVO_COUNT] = {};
  float claimedTargets_[SERVO_COUNT] = {};
  bool jointEver_[SERVO_COUNT] = {};
  uint32_t jointLastMs_[SERVO_COUNT] = {};
  BumpDir jointDir_[SERVO_COUNT] = {};
  bool anyEver_ = false;
  uint32_t anyLastMs_ = 0;
};

// A stall is pinned on a joint only when exactly one joint's own (non-reflex)
// motion ended within this window: a stall fires BUMP_MOTION_TAIL_MS +
// BUMP_STALL_MS after the last motion when the joint was pressing all along.
constexpr uint32_t BUMP_STALL_BLAME_MS = BUMP_MOTION_TAIL_MS + BUMP_STALL_MS + 100;

// The one joint a stall can be pinned on, or -1 when blame is uncertain (none,
// or several): then the caller releases the outputs instead of narrowing.
int stallBlameJoint(const MotionTracker& motion, uint32_t nowMs);

// Which limit a stalled joint is pressing on: the side of its saved mid it sits
// on (never the last slew, which may be a back-off). False when it sits at mid.
bool stallDir(float angle, float savedMid, BumpDir& out);

constexpr size_t BUMP_LOG_CAPACITY = 32;
constexpr size_t BUMP_ANIM_NAME_LEN = 15;

struct BumpEntry {
  uint32_t tMs;
  uint8_t joint;
  float angle;
  BumpDir dir;
  float currentMa;
  bool stall;  // static stall (no motion) rather than a bump while moving
  char anim[BUMP_ANIM_NAME_LEN + 1];
};

// Fixed ring buffer; at(0) is the newest entry.
class BumpLog {
public:
  void push(const BumpEntry& entry);
  void clear();
  size_t size() const { return count_; }
  const BumpEntry& at(size_t newestFirstIndex) const;

  // Fills an entry; `anim` may be nullptr and is truncated to fit.
  static BumpEntry make(
    uint32_t tMs,
    int joint,
    float angle,
    BumpDir dir,
    float currentMa,
    const char* anim,
    bool stall = false
  );

private:
  BumpEntry entries_[BUMP_LOG_CAPACITY] = {};
  size_t head_ = 0;  // next write slot
  size_t count_ = 0;
};
