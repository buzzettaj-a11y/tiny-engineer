// Chip routes (chip/PROTOCOL.md section 4): /mic, /pose, /ranges,
// /ranges/explore, /bumps/clear.

#include <Arduino.h>

#include <cmath>

#include "animation.h"
#include "animation/util.h"
#include "api/busy.h"
#include "display/oled.h"
#include "hardware/servo_wrapper.h"
#include "http/handlers.h"
#include "http/request.h"
#include "http/response.h"
#include "mic/mic.h"
#include "motion/pose_move.h"
#include "motion/reflex.h"
#include "servos.h"
#include "settings/settings.h"
#include "sleep.h"

namespace {

// One decimal place on the wire for angles and currents.
float round1(float value) {
  return roundf(value * 10.0f) / 10.0f;
}

void addFloatArray(JsonDocument& doc, const char* key, const float* values) {
  JsonArray array = doc[key].to<JsonArray>();

  for (int i = 0; i < SERVO_COUNT; i++) {
    array.add(round1(values[i]));
  }
}

void sendMicStatus(WebServer& server) {
  const MicStatus status = micStatus();
  JsonDocument body;
  body["ok"] = true;
  body["muted"] = status.muted;
  body["streaming"] = status.streaming;
  body["present"] = status.present;
  body["level"] = roundf(status.level * 1000.0f) / 1000.0f;
  sendJson(server, 200, body);
}

JsonDocument rangesJson() {
  float saved[2][SERVO_COUNT];
  float soft[2][SERVO_COUNT];

  for (int i = 0; i < SERVO_COUNT; i++) {
    saved[0][i] = settingsServoMin(i);
    saved[1][i] = settingsServoMax(i);
    soft[0][i] = servoSoftMin(i);
    soft[1][i] = servoSoftMax(i);
  }

  JsonDocument doc;
  doc["ok"] = true;
  addFloatArray(doc, "saved_mins", saved[0]);
  addFloatArray(doc, "saved_maxs", saved[1]);
  addFloatArray(doc, "soft_mins", soft[0]);
  addFloatArray(doc, "soft_maxs", soft[1]);
  addFloatArray(doc, "hard_mins", SERVO_HARD_MIN_DEG);
  addFloatArray(doc, "hard_maxs", SERVO_HARD_MAX_DEG);

  float currentMa = 0.0f;

  if (motionCurrentMa(currentMa)) {
    doc["current_ma"] = round1(currentMa);
  } else {
    doc["current_ma"] = nullptr;
  }

  doc["sensor"] = motionHasCurrentSensor();
  doc["bump_threshold_ma"] = settingsBumpThresholdMa();

  const BumpLog& bumps = motionBumps();
  JsonArray list = doc["bumps"].to<JsonArray>();

  for (size_t i = 0; i < bumps.size(); i++) {
    const BumpEntry& entry = bumps.at(i);
    JsonObject item = list.add<JsonObject>();
    item["t_ms"] = entry.tMs;
    item["joint"] = entry.joint;
    item["angle"] = round1(entry.angle);
    item["dir"] = bumpDirName(entry.dir);
    item["current_ma"] = round1(entry.currentMa);
    item["anim"] = entry.anim;
    item["kind"] = entry.stall ? "stall" : "bump";
  }

  return doc;
}

// Five integers in degrees: numbers are rounded to the nearest degree, since
// the NVS calibration stores whole degrees.
bool readDegreeArray(JsonVariantConst list, uint8_t (&out)[SERVO_COUNT]) {
  if (!list.is<JsonArrayConst>() || list.size() != SERVO_COUNT) {
    return false;
  }

  for (int i = 0; i < SERVO_COUNT; i++) {
    if (!list[i].is<float>()) {
      return false;
    }

    const float deg = list[i].as<float>();

    if (!std::isfinite(deg) || deg < 0.0f || deg > static_cast<float>(SETTINGS_SERVO_ANGLE_MAX)) {
      return false;
    }

    out[i] = static_cast<uint8_t>(lroundf(deg));
  }

  return true;
}

}  // namespace

void handleMicGet(WebServer& server) {
  sendMicStatus(server);
}

void handleMicPost(WebServer& server) {
  JsonDocument body;

  if (!readJsonBody(server, body)) {
    return;
  }

  const JsonVariantConst muted = body.as<JsonObjectConst>()["muted"];

  if (!muted.is<bool>()) {
    sendError(server, 400, "invalid muted");
    return;
  }

  micSetMuted(muted.as<bool>());
  sendMicStatus(server);
}

void handlePosePost(WebServer& server) {
  JsonDocument body;

  if (!readJsonBody(server, body)) {
    return;
  }

  const JsonObjectConst fields = body.as<JsonObjectConst>();
  const JsonVariantConst pose = fields["pose"];
  const JsonVariantConst ms = fields["ms"];
  PoseRequest request{};

  if (!pose.is<JsonArrayConst>() || pose.size() != SERVO_COUNT) {
    sendError(server, 400, "invalid pose");
    return;
  }

  for (int i = 0; i < SERVO_COUNT; i++) {
    const JsonVariantConst value = pose[i];

    if (value.isNull()) {
      request.set[i] = false;
      continue;
    }

    if (!value.is<float>() || !poseNormValid(value.as<float>())) {
      sendError(server, 400, "invalid pose");
      return;
    }

    request.set[i] = true;
    request.norm[i] = value.as<float>();
  }

  if (ms.isUnbound()) {
    request.ms = POSE_MS_DEFAULT;
  } else if (!ms.is<uint32_t>() || !poseMsValid(ms.as<uint32_t>())) {
    sendError(server, 400, "invalid ms");
    return;
  } else {
    request.ms = ms.as<uint32_t>();
  }

  // Serial /play holds the robot while loop() keeps running.
  if (apiBusyHeld()) {
    sendError(server, 503, "busy");
    return;
  }

  const uint32_t now = millis();
  // Activity like /anim: wakes the OLED and eyes when asleep.
  wakeForActivity(now);
  setAnimationImmediately(AnimationId::None);
  // none parks every joint; joints left out of the pose stay where they are.
  anim::stopAnimServos();
  poseMoveStart(request, now);

  JsonDocument reply;
  reply["ok"] = true;
  sendJson(server, 200, reply);
}

void handleRangesGet(WebServer& server) {
  sendJson(server, 200, rangesJson());
}

void handleRangesPost(WebServer& server) {
  JsonDocument body;

  if (!readJsonBody(server, body)) {
    return;
  }

  const JsonObjectConst fields = body.as<JsonObjectConst>();
  const bool hasMins = !fields["servo_mins"].isUnbound();
  const bool hasMaxs = !fields["servo_maxs"].isUnbound();

  if (!hasMins || !hasMaxs) {
    sendError(server, 400, "servo_mins and servo_maxs go together");
    return;
  }

  uint8_t mins[SERVO_COUNT] = {};
  uint8_t maxs[SERVO_COUNT] = {};

  if (!readDegreeArray(fields["servo_mins"], mins)) {
    sendError(server, 400, "invalid servo_mins");
    return;
  }

  if (!readDegreeArray(fields["servo_maxs"], maxs)) {
    sendError(server, 400, "invalid servo_maxs");
    return;
  }

  char reason[64];

  if (motionSaveRanges(mins, maxs, reason, sizeof(reason)) != nullptr) {
    sendError(server, 400, reason);
    return;
  }

  sendJson(server, 200, rangesJson());
}

void handleRangesExplore(WebServer& server) {
  JsonDocument body;

  if (!readJsonBody(server, body)) {
    return;
  }

  const JsonObjectConst fields = body.as<JsonObjectConst>();
  const JsonVariantConst joint = fields["joint"];
  const JsonVariantConst speedField = fields["speed"];
  BumpDir dir = BumpDir::Max;
  float speed = EXPLORE_SPEED_DEFAULT_DEG_S;

  if (!joint.is<int>() || joint.as<int>() < 0 || joint.as<int>() >= SERVO_COUNT) {
    sendError(server, 400, "invalid joint");
    return;
  }

  if (!parseBumpDir(fields["dir"].as<const char*>(), dir)) {
    sendError(server, 400, "invalid dir");
    return;
  }

  if (!speedField.isUnbound()) {
    if (!speedField.is<float>()) {
      sendError(server, 400, "invalid speed");
      return;
    }

    speed = speedField.as<float>();

    if (!std::isfinite(speed) || speed < EXPLORE_SPEED_MIN_DEG_S || speed > EXPLORE_SPEED_MAX_DEG_S) {
      sendError(server, 400, "invalid speed");
      return;
    }
  }

  if (apiBusyHeld()) {
    sendError(server, 503, "busy");
    return;
  }

  const ExploreResult result = motionExplore(joint.as<int>(), dir, speed);

  if (result.code != 200) {
    sendError(server, result.code, result.error);
    return;
  }

  JsonDocument reply;
  reply["ok"] = true;
  reply["joint"] = result.joint;
  reply["dir"] = bumpDirName(result.dir);
  reply["stopped_at"] = round1(result.stoppedAt);
  reply["reason"] = result.reason;
  reply["safe"] = round1(result.safe);
  reply["peak_ma"] = round1(result.peakMa);
  sendJson(server, 200, reply);
}

void handleBumpsClear(WebServer& server) {
  motionClearBumps();

  JsonDocument reply;
  reply["ok"] = true;
  sendJson(server, 200, reply);
}
