#include "motion/bump_detector.h"

#include <cmath>
#include <cstring>

namespace {

// True while `since` is less than `windowMs` ago. Signed, so a stamp taken a
// little after `now` (another call site read millis() later) still counts.
bool within(uint32_t now, uint32_t since, uint32_t windowMs) {
  return static_cast<int32_t>(now - since) < static_cast<int32_t>(windowMs);
}

}  // namespace

// BumpDetector ---------------------------------------------------------------

void BumpDetector::closeWindows() {
  windowOpen_ = false;
  windowMoving_ = false;
  windowAnglesValid_ = false;
}

void BumpDetector::openWindow(const BumpSample& sample) {
  windowOpen_ = true;
  windowMoving_ = sample.motion;
  windowSinceMs_ = sample.nowMs;
  windowPeakMa_ = sample.currentMa;
  windowAnglesValid_ = sample.angles != nullptr;

  if (windowAnglesValid_) {
    memcpy(windowAngles_, sample.angles, sizeof(windowAngles_));
  }
}

BumpEvent BumpDetector::fire(BumpEvent event, const BumpSample& sample) {
  if (!windowOpen_) {
    openWindow(sample);
  }

  if (sample.currentMa > windowPeakMa_) {
    windowPeakMa_ = sample.currentMa;
  }

  eventPeakMa_ = windowPeakMa_;
  eventAnglesValid_ = windowAnglesValid_;
  if (eventAnglesValid_) {
    memcpy(eventAngles_, windowAngles_, sizeof(eventAngles_));
  }

  cooling_ = true;
  lastEventMs_ = sample.nowMs;
  closeWindows();
  midOpen_ = false;
  return event;
}

void BumpDetector::learn(Baseline& baseline, float ma) {
  if (!baseline.valid) {
    // Plain average of the first still samples: no seed from a single reading.
    baseline.ma += (ma - baseline.ma) / static_cast<float>(baseline.stillSamples + 1);
    baseline.stillSamples++;
    baseline.valid = baseline.stillSamples >= BUMP_ARM_STILL_SAMPLES;
    return;
  }

  baseline.ma += BUMP_BASELINE_ALPHA * (ma - baseline.ma);
}

BumpEvent BumpDetector::update(const BumpSample& sample) {
  // Outputs switched on or off: the other baseline applies, old windows are void.
  if (seenSample_ && sample.outputsOn != lastOutputsOn_) {
    closeWindows();
    midOpen_ = false;
  }
  seenSample_ = true;
  lastOutputsOn_ = sample.outputsOn;

  if (cooling_ && !within(sample.nowMs, lastEventMs_, BUMP_COOLDOWN_MS)) {
    cooling_ = false;
  }

  Baseline& baseline = baselineFor(sample.outputsOn);
  const bool enabled = sample.thresholdMa > 0;
  const float threshold = static_cast<float>(sample.thresholdMa);
  const float ma = sample.currentMa;

  if (sample.motion) {
    // Moving: the baseline is frozen (motion current is not idle current).
    midOpen_ = false;

    if (windowOpen_ && !windowMoving_) {
      closeWindows();
    }

    if (!enabled || cooling_) {
      closeWindows();
      return BumpEvent::None;
    }

    // The absolute trip needs no baseline.
    if (ma >= BUMP_ABSOLUTE_TRIP_MA) {
      return fire(BumpEvent::Bump, sample);
    }

    // Never armed from a moving sample: no baseline for this state yet = only
    // the absolute trip guards the move.
    if (!baseline.valid) {
      closeWindows();
      return BumpEvent::None;
    }

    if (ma <= baseline.ma + threshold) {
      closeWindows();
      return BumpEvent::None;
    }

    if (!windowOpen_) {
      openWindow(sample);
      return BumpEvent::None;
    }

    if (ma > windowPeakMa_) {
      windowPeakMa_ = ma;
    }

    if (!within(sample.nowMs, windowSinceMs_, BUMP_HOLD_MS)) {
      return fire(BumpEvent::Bump, sample);
    }

    return BumpEvent::None;
  }

  // Still.
  if (windowOpen_ && windowMoving_) {
    closeWindows();
  }

  if (enabled && sample.outputsOn && !cooling_ && ma >= BUMP_ABSOLUTE_TRIP_MA) {
    return fire(BumpEvent::Stall, sample);
  }

  if (!baseline.valid || !enabled) {
    learn(baseline, ma);
    midOpen_ = false;
    closeWindows();
    return BumpEvent::None;
  }

  const float over = ma - baseline.ma;

  if (over <= threshold * 0.5f) {
    learn(baseline, ma);
    midOpen_ = false;
    closeWindows();
    return BumpEvent::None;
  }

  if (over <= threshold) {
    // A new holding load (e.g. a pose against gravity) is accepted slowly; a
    // stall-level current never enters the baseline.
    closeWindows();
    if (!midOpen_) {
      midOpen_ = true;
      midSinceMs_ = sample.nowMs;
    } else if (!within(sample.nowMs, midSinceMs_, BUMP_BASELINE_SLOW_ACCEPT_MS)) {
      learn(baseline, ma);
    }
    return BumpEvent::None;
  }

  midOpen_ = false;

  // Limp servos cannot stall; inside the cooldown, wait.
  if (!sample.outputsOn || cooling_) {
    closeWindows();
    return BumpEvent::None;
  }

  if (!windowOpen_) {
    openWindow(sample);
    return BumpEvent::None;
  }

  if (ma > windowPeakMa_) {
    windowPeakMa_ = ma;
  }

  if (!within(sample.nowMs, windowSinceMs_, BUMP_STALL_MS)) {
    return fire(BumpEvent::Stall, sample);
  }

  return BumpEvent::None;
}

void BumpDetector::reset() {
  *this = BumpDetector();
}

bool BumpDetector::armed(bool outputsOn) const {
  return outputsOn ? on_.valid : off_.valid;
}

float BumpDetector::baselineMa(bool outputsOn) const {
  return outputsOn ? on_.ma : off_.ma;
}

float BumpDetector::eventAngle(int joint) const {
  if (joint < 0 || joint >= SERVO_COUNT) {
    return 0.0f;
  }
  return eventAngles_[joint];
}

// MotionTracker --------------------------------------------------------------

void MotionTracker::observe(
  uint32_t nowMs,
  const float angles[SERVO_COUNT],
  const float targets[SERVO_COUNT]
) {
  for (int j = 0; j < SERVO_COUNT; j++) {
    const bool slewing = std::fabs(targets[j] - angles[j]) >= SERVO_ANGLE_DEADBAND_DEG;

    if (claimed_[j] && targets[j] == claimedTargets_[j]) {
      // Reflex-owned move: "moving" for the detector, not blamable.
      if (slewing) {
        anyEver_ = true;
        anyLastMs_ = nowMs;
      }
      lastTargets_[j] = targets[j];
      continue;
    }

    claimed_[j] = false;
    const bool retargeted = seen_ && targets[j] != lastTargets_[j];

    if (slewing || retargeted) {
      if (slewing) {
        jointDir_[j] = travelDir(angles[j], targets[j]);
      } else {
        jointDir_[j] = targets[j] > lastTargets_[j] ? BumpDir::Max : BumpDir::Min;
      }

      jointEver_[j] = true;
      jointLastMs_[j] = nowMs;
      anyEver_ = true;
      anyLastMs_ = nowMs;
    }

    lastTargets_[j] = targets[j];
  }

  seen_ = true;
}

void MotionTracker::claim(const float targets[SERVO_COUNT]) {
  for (int j = 0; j < SERVO_COUNT; j++) {
    claimed_[j] = true;
    claimedTargets_[j] = targets[j];
    lastTargets_[j] = targets[j];
  }
}

void MotionTracker::noteMotion(uint32_t nowMs) {
  anyEver_ = true;
  anyLastMs_ = nowMs;
}

bool MotionTracker::active(uint32_t nowMs) const {
  return anyEver_ && within(nowMs, anyLastMs_, BUMP_MOTION_TAIL_MS);
}

bool MotionTracker::jointRecent(int joint, uint32_t nowMs, uint32_t windowMs) const {
  if (joint < 0 || joint >= SERVO_COUNT) {
    return false;
  }
  return jointEver_[joint] && within(nowMs, jointLastMs_[joint], windowMs);
}

BumpDir MotionTracker::jointDir(int joint) const {
  if (joint < 0 || joint >= SERVO_COUNT) {
    return BumpDir::Max;
  }
  return jointDir_[joint];
}

int stallBlameJoint(const MotionTracker& motion, uint32_t nowMs) {
  int found = -1;

  for (int j = 0; j < SERVO_COUNT; j++) {
    if (!motion.jointRecent(j, nowMs, BUMP_STALL_BLAME_MS)) {
      continue;
    }
    if (found >= 0) {
      return -1;
    }
    found = j;
  }

  return found;
}

bool stallDir(float angle, float savedMid, BumpDir& out) {
  if (std::fabs(angle - savedMid) < SERVO_ANGLE_DEADBAND_DEG) {
    return false;
  }
  out = angle > savedMid ? BumpDir::Max : BumpDir::Min;
  return true;
}

// BumpLog --------------------------------------------------------------------

void BumpLog::push(const BumpEntry& entry) {
  entries_[head_] = entry;
  head_ = (head_ + 1) % BUMP_LOG_CAPACITY;
  if (count_ < BUMP_LOG_CAPACITY) {
    count_++;
  }
}

void BumpLog::clear() {
  head_ = 0;
  count_ = 0;
}

const BumpEntry& BumpLog::at(size_t newestFirstIndex) const {
  const size_t index = (head_ + BUMP_LOG_CAPACITY - 1 - (newestFirstIndex % BUMP_LOG_CAPACITY)) %
                       BUMP_LOG_CAPACITY;
  return entries_[index];
}

BumpEntry BumpLog::make(
  uint32_t tMs,
  int joint,
  float angle,
  BumpDir dir,
  float currentMa,
  const char* anim,
  bool stall
) {
  BumpEntry entry{};
  entry.stall = stall;
  entry.tMs = tMs;
  entry.joint = static_cast<uint8_t>(joint);
  entry.angle = angle;
  entry.dir = dir;
  entry.currentMa = currentMa;
  if (anim != nullptr) {
    strncpy(entry.anim, anim, BUMP_ANIM_NAME_LEN);
    entry.anim[BUMP_ANIM_NAME_LEN] = '\0';
  }
  return entry;
}
