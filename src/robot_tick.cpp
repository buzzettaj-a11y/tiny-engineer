#include "robot_tick.h"

#include <Arduino.h>

#include "animation.h"
#include "display/oled.h"
#include "hardware/activity.h"
#include "hardware/rgb.h"
#include "hardware/servo_wrapper.h"
#include "mic/mic.h"
#include "motion/pose_move.h"
#include "motion/reflex.h"
#include "network/wifi_connect.h"
#include "sleep.h"

void tickRobot() {
  const uint32_t now = millis();
  pollWifi();
  updateProvisioningOled(now);
  // Servo-rail current and the bump reflex run before the animation so a bump
  // freezes joints before this tick's servo steps.
  motionTick(now);
  poseMoveUpdate(now);
  updateAnimation();
  // Read again: an animation applied this tick stamps its idle time after `now`.
  updateSleep(millis());
  updateRgb(now);
  pollServoPoseSave();
  activityPublish(anyServoMoving(), isSleeping(), wifiConnected());
  micTick(millis());
}
