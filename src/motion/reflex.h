#pragma once

#include <cstddef>
#include <cstdint>

#include "motion/bump_detector.h"
#include "motion/servo_limits.h"

// Runtime servo guard (chip/PROTOCOL.md sections 4–5): soft limits, servo-rail
// current, the bump reflex, POST /ranges and POST /ranges/explore. Loop task only.

// Soft limits follow the saved calibration automatically: when the NVS ranges
// change (setup wizard, POST /ranges, factory reset) they reset to it.
float servoSoftMin(int joint);
float servoSoftMax(int joint);
float servoSoftClamp(int joint, float deg);
void servoSoftLimitsReset();

// tickRobot(): reads the INA219 (at most every kCurrentPollMs) and runs the reflex.
void motionTick(uint32_t nowMs);

bool motionHasCurrentSensor();
// Latest servo-rail current; false without a sensor or before the first read.
bool motionCurrentMa(float& outMa);

const BumpLog& motionBumps();
// Empties the bump list and resets soft limits to saved.
void motionClearBumps();

// POST /ranges: validates integer degrees against the hard caps and minimum
// span, saves them to NVS (outside setup mode too) and resets the soft limits.
// Returns nullptr on success, else the reason (written into `reason`).
const char* motionSaveRanges(
  const uint8_t mins[SERVO_COUNT],
  const uint8_t maxs[SERVO_COUNT],
  char* reason,
  size_t reasonSize
);

constexpr float EXPLORE_SPEED_MIN_DEG_S = 5.0f;
constexpr float EXPLORE_SPEED_MAX_DEG_S = 30.0f;
constexpr float EXPLORE_SPEED_DEFAULT_DEG_S = 10.0f;

struct ExploreResult {
  int code;            // 200, or the HTTP error status
  const char* error;   // set when code != 200
  int joint;
  BumpDir dir;
  float stoppedAt;
  bool bumped;         // reason: "bump" when true, else "hard_limit"
  float safe;
  float peakMa;
};

// Blocking. Stops animations, holds the other joints, drives `joint` from its
// saved limit toward the hard cap at `speedDegS` until a bump or the cap, backs
// off SERVO_BUMP_BACKOFF_DEG, then returns the joint inside its soft limits.
// Saves nothing.
ExploreResult motionExplore(int joint, BumpDir dir, float speedDegS);
