#pragma once

#include <cstdint>

#include "animation.h"

void initSleep();
void prepareSleepWakePose();
void requestSleep(uint32_t now);
void onAnimationApplied(AnimationId id, uint32_t now);
// Restarts the idle countdown, e.g. while a clip plays over the idle pose.
void noteActivity(uint32_t now);
// Activity that is not an animation (POST /pose): restarts the idle clock and
// opens the eyes / wakes the OLED like a non-none animation would.
void wakeForActivity(uint32_t now);
void updateSleep(uint32_t now);
bool isSleeping();
