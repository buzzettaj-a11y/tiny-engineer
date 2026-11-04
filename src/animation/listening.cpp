#include <Arduino.h>

#include "animation/constants.h"
#include "animation/listening.h"
#include "animation/util.h"
#include "hardware/servo_wrapper.h"
#include "network/wifi_connect.h"
#include "servos.h"

namespace {

bool g_stillTracking = false;
uint32_t g_stillSinceMs = 0;

}  // namespace

// Faces the user and then holds completely still: no fidget, and PWM is
// released after the usual hold so servo noise stays off the mic.
void startListening() {
  anim::stopAnimServos();
  g_stillTracking = false;
  g_stillSinceMs = 0;
  anim::parkTorso(anim::TRANSITION_TORSO_SPEED_DEG_S);
  anim::parkHands(anim::TRANSITION_HAND_SPEED_DEG_S);
  servoAt(SERVO_HEAD).setNormTarget(
    anim::LISTENING_HEAD_UP,
    anim::TRANSITION_TORSO_SPEED_DEG_S
  );
}

void updateListening(uint32_t now) {
  updateAllServos();

  if (wifiProvisioningMode() || anyServoMoving()) {
    g_stillTracking = false;
    return;
  }

  if (!g_stillTracking) {
    g_stillTracking = true;
    g_stillSinceMs = now;
    return;
  }

  if (now - g_stillSinceMs >= SERVO_PWM_RELEASE_DELAY_MS) {
    releaseAllServoOutputs();
  }
}
