#pragma once

#include <cstddef>
#include <cstdint>

#include "motion/servo_limits.h"

// Current-based bump detection (chip/PROTOCOL.md section 5).
// Pure logic, no Arduino dependencies (host-tested).

// Over threshold for at least this long while moving = bump.
constexpr uint32_t BUMP_HOLD_MS = 60;
// Bumps are ignored this long after one fires.
constexpr uint32_t BUMP_COOLDOWN_MS = 500;
// Idle baseline EMA weight per sample (~0.3 s time constant at 10–20 ms reads).
constexpr float BUMP_BASELINE_ALPHA = 0.05f;

class BumpDetector {
public:
  // Feed one current sample. `moving` = at least one servo is moving.
  // thresholdMa == 0 disables detection (the baseline still tracks).
  // Returns true on the sample that completes a bump.
  bool update(uint32_t nowMs, float currentMa, bool moving, uint16_t thresholdMa);

  void reset();

  bool baselineValid() const { return baselineValid_; }
  float baselineMa() const { return baselineMa_; }
  // Highest current of the over-threshold window that fired the last bump.
  float bumpPeakMa() const { return bumpPeakMa_; }

private:
  bool baselineValid_ = false;
  float baselineMa_ = 0.0f;
  bool over_ = false;
  uint32_t overSinceMs_ = 0;
  float windowPeakMa_ = 0.0f;
  float bumpPeakMa_ = 0.0f;
  bool cooling_ = false;
  uint32_t lastBumpMs_ = 0;
};

constexpr size_t BUMP_LOG_CAPACITY = 32;
constexpr size_t BUMP_ANIM_NAME_LEN = 15;

struct BumpEntry {
  uint32_t tMs;
  uint8_t joint;
  float angle;
  BumpDir dir;
  float currentMa;
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
    const char* anim
  );

private:
  BumpEntry entries_[BUMP_LOG_CAPACITY] = {};
  size_t head_ = 0;  // next write slot
  size_t count_ = 0;
};
