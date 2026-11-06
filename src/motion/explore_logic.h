#pragma once

#include <cstdint>

#include "motion/servo_limits.h"

// POST /ranges/explore bounds (chip/PROTOCOL.md section 4). Pure, host-tested.

// The sweep goes at most this far past the saved limit (and never past hard).
constexpr float EXPLORE_MAX_EXCURSION_DEG = 30.0f;
// Bump threshold used while exploring: min(bump_threshold_ma, this). A setting
// of 0 (reflex off) explores with this value.
constexpr uint16_t EXPLORE_THRESHOLD_CAP_MA = 250;
// After the settle, the powered-but-still current must look like real servo
// holding current. Below the minimum (incl. negative) = shunt wired backwards
// (VIN+/VIN- swapped) or not in the servo lead.
constexpr float EXPLORE_MIN_BASELINE_MA = 10.0f;
constexpr float EXPLORE_MAX_BASELINE_MA = 1500.0f;
// Consecutive failed current reads that abort an explore.
constexpr uint8_t EXPLORE_MAX_READ_FAILURES = 5;
// Approach + sweep stop after this long (reason "max_travel"); the back-off
// and return that follow add at most ~2 s.
constexpr uint32_t EXPLORE_TIME_LIMIT_MS = 12000;

struct ExploreBound {
  float target;
  // "max_travel": the excursion cap, not the hard cap, ends the sweep.
  bool excursionCapped;
};

// Sweep end for a joint whose saved limit in `dir` is `savedLimit`.
ExploreBound exploreSweepTarget(float savedLimit, float hardLimit, BumpDir dir, float maxExcursionDeg);

uint16_t exploreThresholdMa(uint16_t bumpThresholdSetting);

bool exploreBaselinePlausible(float baselineMa);
