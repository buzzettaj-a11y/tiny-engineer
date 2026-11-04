#pragma once

#include <cstdint>

#include "motion/pose_logic.h"

// POST /pose: one eased free-form move in animation `none`. Loop task only.

// Starts the move from the current angles. Joints with set[j] == false are
// left alone. The duration stretches so SERVO_MAX_SPEED_DEG_S still holds.
void poseMoveStart(const PoseRequest& request, uint32_t nowMs);
// tickRobot(), before updateAnimation(): feeds each joint its eased target.
// Ends on its own when the move is done or another animation starts.
void poseMoveUpdate(uint32_t nowMs);
void poseMoveCancel();
bool poseMoveActive();
