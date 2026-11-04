#pragma once

#include <stdint.h>

#include "pins.h"
#include "servos.h"

class ServoWrapper {
public:
  explicit ServoWrapper(int index);

  float angle() const;
  // Where the joint is heading (== angle() when still).
  float target() const;
  bool moveTo(float target);
  bool moveToElectrical(float target, float speedDegS);
  void snapTo(float angle);
  // POST /ranges/explore only: writes `angle` clamped to the hard caps
  // (SERVO_HARD_*_DEG), past the soft limits. Joint stops there.
  void driveHardClamped(float angle);

  void setTarget(
    float target,
    float speedDegS = SERVO_MAX_SPEED_DEG_S
  );
  void setPosition(float angle);
  void setNormTarget(
    float n,
    float speedDegS = SERVO_MAX_SPEED_DEG_S
  );
  void setNormPosition(float n);
  void stop();
  void update();
  bool isMoving() const;
  void ensureServoOutput();
  void releaseServoOutput();

  friend void servoMoveAllSmoothTo(
    const float targets[SERVO_COUNT],
    float speedDegS
  );
  friend void servoMoveAllToElectrical(
    const float targets[SERVO_COUNT],
    float speedDegS
  );

private:
  int index_;
  float angle_;
  float target_;
  float speedDegS_;
  uint32_t lastUpdateMs_;
  uint16_t lastPulse_;
  bool released_;

  void writeAngle(float angle, bool log, bool electrical);
};

ServoWrapper& servoAt(int index);
void initServoOutputPin();
void disableServoOutputs();
void enableServoOutputs();
void initServoPwmDriver();
void servoMoveAllSmoothTo(
  const float targets[SERVO_COUNT],
  float speedDegS = SERVO_BOOT_SPEED_DEG_S
);
void servoMoveAllToElectrical(
  const float targets[SERVO_COUNT],
  float speedDegS = SERVO_CALIB_SPEED_DEG_S
);
void updateAllServos();
bool anyServoMoving();
void ensureAllServoOutputs();
void releaseAllServoOutputs();

// Persist last commanded angles; true when saved or already up to date.
bool flushServoPoseToNvs();
void pollServoPoseSave();
void setServoParkHoldUntilReboot(bool hold);
// millis() when a released PWM output was last switched back on (or boot).
// A limp joint that drooped snaps back then, drawing a short current spike.
uint32_t servoOutputsLastWakeMs();
// True after releaseAllServoOutputs() until any output is switched on again.
bool servoOutputsReleased();
bool servoOutputsHoldActive();

float clampElectricalAngle(float angle);
// Clamps to the joint's soft limits (motion/reflex.h).
float clampServoAngle(int index, float angle);
float servoRuntimeMid(int index);
float servoNormToDeg(int index, float n);
float servoDegToNorm(int index, float deg);
