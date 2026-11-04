#include <unity.h>

#include <cmath>
#include <limits>

#include "motion/pose_logic.h"
#include "servos.h"

void test_norm_validation() {
  TEST_ASSERT_TRUE(poseNormValid(0.0f));
  TEST_ASSERT_TRUE(poseNormValid(-1.0f));
  TEST_ASSERT_TRUE(poseNormValid(1.0f));
  TEST_ASSERT_TRUE(poseNormValid(0.3f));
  TEST_ASSERT_FALSE(poseNormValid(1.01f));
  TEST_ASSERT_FALSE(poseNormValid(-1.01f));
  TEST_ASSERT_FALSE(poseNormValid(std::numeric_limits<float>::quiet_NaN()));
  TEST_ASSERT_FALSE(poseNormValid(std::numeric_limits<float>::infinity()));
}

void test_ms_validation() {
  TEST_ASSERT_FALSE(poseMsValid(99));
  TEST_ASSERT_TRUE(poseMsValid(100));
  TEST_ASSERT_TRUE(poseMsValid(600));
  TEST_ASSERT_TRUE(poseMsValid(5000));
  TEST_ASSERT_FALSE(poseMsValid(5001));
  TEST_ASSERT_TRUE(poseMsValid(POSE_MS_DEFAULT));
}

void test_effective_ms_keeps_request_when_slow_enough() {
  // 20 deg in 600 ms: peak 3 * 20 / 0.6 = 100 deg/s < 140.
  TEST_ASSERT_EQUAL_UINT32(600, poseEffectiveMs(600, 20.0f, SERVO_MAX_SPEED_DEG_S));
  TEST_ASSERT_EQUAL_UINT32(600, poseEffectiveMs(600, 0.0f, SERVO_MAX_SPEED_DEG_S));
}

void test_effective_ms_stretches_to_respect_max_speed() {
  // 70 deg in 100 ms would peak at 2100 deg/s; needs 3 * 70 / 140 = 1.5 s.
  TEST_ASSERT_EQUAL_UINT32(1500, poseEffectiveMs(100, 70.0f, SERVO_MAX_SPEED_DEG_S));
  const uint32_t ms = poseEffectiveMs(200, 33.3f, SERVO_MAX_SPEED_DEG_S);
  // Peak speed of the stretched move stays at or under the cap.
  TEST_ASSERT_TRUE(3.0f * 33.3f / (ms / 1000.0f) <= SERVO_MAX_SPEED_DEG_S + 0.01f);
}

void test_interpolate_ends_and_midpoint() {
  TEST_ASSERT_EQUAL_FLOAT(80.0f, poseInterpolate(80, 120, 0, 600));
  TEST_ASSERT_EQUAL_FLOAT(100.0f, poseInterpolate(80, 120, 300, 600));
  TEST_ASSERT_EQUAL_FLOAT(120.0f, poseInterpolate(80, 120, 600, 600));
  TEST_ASSERT_EQUAL_FLOAT(120.0f, poseInterpolate(80, 120, 9999, 600));
  TEST_ASSERT_EQUAL_FLOAT(120.0f, poseInterpolate(80, 120, 0, 0));
}

void test_interpolate_monotonic_and_eased() {
  float last = 50.0f;
  for (uint32_t t = 0; t <= 1000; t += 10) {
    const float v = poseInterpolate(50, 10, t, 1000);
    TEST_ASSERT_TRUE(v <= last + 1e-4f);
    last = v;
  }
  // Ease-in: the first 10% covers much less than 10% of the way.
  TEST_ASSERT_TRUE(poseInterpolate(0, 100, 100, 1000) < 2.0f);
}

void test_interpolated_speed_respects_cap() {
  const float delta = 90.0f;
  const uint32_t ms = poseEffectiveMs(100, delta, SERVO_MAX_SPEED_DEG_S);
  float maxSpeed = 0.0f;
  for (uint32_t t = 0; t + 1 <= ms; t++) {
    const float step = poseInterpolate(0, delta, t + 1, ms) - poseInterpolate(0, delta, t, ms);
    maxSpeed = fmaxf(maxSpeed, step * 1000.0f);
  }
  TEST_ASSERT_TRUE(maxSpeed <= SERVO_MAX_SPEED_DEG_S * 1.01f);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_norm_validation);
  RUN_TEST(test_ms_validation);
  RUN_TEST(test_effective_ms_keeps_request_when_slow_enough);
  RUN_TEST(test_effective_ms_stretches_to_respect_max_speed);
  RUN_TEST(test_interpolate_ends_and_midpoint);
  RUN_TEST(test_interpolate_monotonic_and_eased);
  RUN_TEST(test_interpolated_speed_respects_cap);
  return UNITY_END();
}
