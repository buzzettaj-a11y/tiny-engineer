#include "motion/bump_detector.h"

#include <cstring>

bool BumpDetector::update(uint32_t nowMs, float currentMa, bool moving, uint16_t thresholdMa) {
  if (!baselineValid_) {
    baselineMa_ = currentMa;
    baselineValid_ = true;
  }

  if (cooling_ && static_cast<uint32_t>(nowMs - lastBumpMs_) >= BUMP_COOLDOWN_MS) {
    cooling_ = false;
  }

  if (!moving) {
    baselineMa_ += BUMP_BASELINE_ALPHA * (currentMa - baselineMa_);
    over_ = false;
    return false;
  }

  if (thresholdMa == 0 || cooling_) {
    over_ = false;
    return false;
  }

  if (currentMa <= baselineMa_ + static_cast<float>(thresholdMa)) {
    over_ = false;
    return false;
  }

  if (!over_) {
    over_ = true;
    overSinceMs_ = nowMs;
    windowPeakMa_ = currentMa;
    return false;
  }

  if (currentMa > windowPeakMa_) {
    windowPeakMa_ = currentMa;
  }

  if (static_cast<uint32_t>(nowMs - overSinceMs_) < BUMP_HOLD_MS) {
    return false;
  }

  over_ = false;
  cooling_ = true;
  lastBumpMs_ = nowMs;
  bumpPeakMa_ = windowPeakMa_;
  return true;
}

void BumpDetector::reset() {
  *this = BumpDetector();
}

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
  const char* anim
) {
  BumpEntry entry{};
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
