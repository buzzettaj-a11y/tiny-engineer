#include <unity.h>

#include <cstring>

#include "motion/servo_limits.h"
#include "servos.h"

namespace {

const float kSavedMins[SERVO_COUNT] = {40, 50, 60, 70, 80};
const float kSavedMaxs[SERVO_COUNT] = {140, 130, 120, 110, 100};

SoftLimits freshLimits() {
  SoftLimits soft{};
  soft.reset(kSavedMins, kSavedMaxs);
  return soft;
}

}  // namespace

void test_hard_caps_inside_electrical_clamp() {
  for (int i = 0; i < SERVO_COUNT; i++) {
    TEST_ASSERT_TRUE(SERVO_HARD_MIN_DEG[i] >= 0.0f);
    TEST_ASSERT_TRUE(SERVO_HARD_MAX_DEG[i] <= 180.0f);
    TEST_ASSERT_TRUE(SERVO_HARD_MIN_DEG[i] < SERVO_HARD_MAX_DEG[i]);
    // Stock defaults stay valid under the caps.
    TEST_ASSERT_TRUE(SERVO_SPECS[i].min >= SERVO_HARD_MIN_DEG[i]);
    TEST_ASSERT_TRUE(SERVO_SPECS[i].max <= SERVO_HARD_MAX_DEG[i]);
  }
}

void test_reset_soft_equals_saved_inside_hard() {
  const SoftLimits soft = freshLimits();
  for (int i = 0; i < SERVO_COUNT; i++) {
    TEST_ASSERT_EQUAL_FLOAT(kSavedMins[i], soft.mins[i]);
    TEST_ASSERT_EQUAL_FLOAT(kSavedMaxs[i], soft.maxs[i]);
  }
}

void test_reset_keeps_saved_past_hard_caps() {
  // Stock behaviour: the runtime clamp is the saved calibration, even when the
  // setup wizard saved it wider than the hard caps.
  const float mins[SERVO_COUNT] = {0, 5, 10, 20, 175};
  const float maxs[SERVO_COUNT] = {180, 175, 170, 160, 180};
  SoftLimits soft{};
  soft.reset(mins, maxs);

  for (int i = 0; i < SERVO_COUNT; i++) {
    TEST_ASSERT_EQUAL_FLOAT(mins[i], soft.mins[i]);
    TEST_ASSERT_EQUAL_FLOAT(maxs[i], soft.maxs[i]);
  }
  TEST_ASSERT_EQUAL_FLOAT(3, soft.clamp(0, 3));
  TEST_ASSERT_EQUAL_FLOAT(178, soft.clamp(0, 178));
}

void test_clamp() {
  const SoftLimits soft = freshLimits();
  TEST_ASSERT_EQUAL_FLOAT(40, soft.clamp(0, 10));
  TEST_ASSERT_EQUAL_FLOAT(140, soft.clamp(0, 179));
  TEST_ASSERT_EQUAL_FLOAT(90, soft.clamp(0, 90));
  TEST_ASSERT_EQUAL_FLOAT(33, soft.clamp(-1, 33));
  TEST_ASSERT_EQUAL_FLOAT(33, soft.clamp(SERVO_COUNT, 33));
}

void test_bump_narrows_max_two_degrees_inside() {
  SoftLimits soft = freshLimits();
  const float limit = soft.narrowAfterBump(0, 130.0f, BumpDir::Max, 90.0f);
  TEST_ASSERT_EQUAL_FLOAT(128, limit);
  TEST_ASSERT_EQUAL_FLOAT(128, soft.maxs[0]);
  TEST_ASSERT_EQUAL_FLOAT(40, soft.mins[0]);
}

void test_bump_narrows_min_two_degrees_inside() {
  SoftLimits soft = freshLimits();
  const float limit = soft.narrowAfterBump(1, 55.0f, BumpDir::Min, 90.0f);
  TEST_ASSERT_EQUAL_FLOAT(57, limit);
  TEST_ASSERT_EQUAL_FLOAT(57, soft.mins[1]);
  TEST_ASSERT_EQUAL_FLOAT(130, soft.maxs[1]);
}

void test_bump_never_past_saved_mid() {
  SoftLimits soft = freshLimits();
  TEST_ASSERT_EQUAL_FLOAT(90, soft.narrowAfterBump(0, 91.0f, BumpDir::Max, 90.0f));
  TEST_ASSERT_EQUAL_FLOAT(90, soft.narrowAfterBump(1, 89.0f, BumpDir::Min, 90.0f));
}

void test_bump_never_widens() {
  SoftLimits soft = freshLimits();
  soft.narrowAfterBump(0, 120.0f, BumpDir::Max, 90.0f);
  TEST_ASSERT_EQUAL_FLOAT(118, soft.maxs[0]);
  // A later bump further out keeps the tighter limit.
  TEST_ASSERT_EQUAL_FLOAT(118, soft.narrowAfterBump(0, 139.0f, BumpDir::Max, 90.0f));
  // A bump outside the soft range (joint pushed past it) does not widen either.
  TEST_ASSERT_EQUAL_FLOAT(40, soft.narrowAfterBump(0, 20.0f, BumpDir::Min, 90.0f));
  TEST_ASSERT_EQUAL_FLOAT(40, soft.mins[0]);
}

void test_backoff_toward_mid() {
  TEST_ASSERT_EQUAL_FLOAT(126, bumpBackoffTarget(130, BumpDir::Max, 90));
  TEST_ASSERT_EQUAL_FLOAT(54, bumpBackoffTarget(50, BumpDir::Min, 90));
  // Closer than 4 degrees to mid: stops at mid.
  TEST_ASSERT_EQUAL_FLOAT(90, bumpBackoffTarget(92, BumpDir::Max, 90));
  TEST_ASSERT_EQUAL_FLOAT(90, bumpBackoffTarget(88, BumpDir::Min, 90));
}

void test_backoff_retreats_from_obstacle_on_return_moves() {
  // Moving toward max but still below mid (returning): back off downward.
  TEST_ASSERT_EQUAL_FLOAT(76, bumpBackoffTarget(80, BumpDir::Max, 90));
  TEST_ASSERT_EQUAL_FLOAT(104, bumpBackoffTarget(100, BumpDir::Min, 90));
}

void test_travel_dir() {
  TEST_ASSERT_TRUE(travelDir(90, 120) == BumpDir::Max);
  TEST_ASSERT_TRUE(travelDir(90, 60) == BumpDir::Min);
}

void test_dir_names() {
  BumpDir dir = BumpDir::Min;
  TEST_ASSERT_TRUE(parseBumpDir("max", dir));
  TEST_ASSERT_TRUE(dir == BumpDir::Max);
  TEST_ASSERT_TRUE(parseBumpDir("min", dir));
  TEST_ASSERT_TRUE(dir == BumpDir::Min);
  TEST_ASSERT_FALSE(parseBumpDir("MAX", dir));
  TEST_ASSERT_FALSE(parseBumpDir("", dir));
  TEST_ASSERT_FALSE(parseBumpDir(nullptr, dir));
  TEST_ASSERT_EQUAL_STRING("max", bumpDirName(BumpDir::Max));
  TEST_ASSERT_EQUAL_STRING("min", bumpDirName(BumpDir::Min));
}

void test_validate_ranges_ok() {
  char reason[64];
  TEST_ASSERT_NULL(validateServoRanges(kSavedMins, kSavedMaxs, SERVO_HARD_MIN_DEG,
                                       SERVO_HARD_MAX_DEG, SERVO_RANGE_MIN_SPAN_DEG,
                                       reason, sizeof(reason)));
  // Exactly the hard caps and exactly the minimum span are allowed.
  const float mins[SERVO_COUNT] = {10, 10, 10, 10, 84};
  const float maxs[SERVO_COUNT] = {170, 170, 170, 16, 90};
  TEST_ASSERT_NULL(validateServoRanges(mins, maxs, SERVO_HARD_MIN_DEG, SERVO_HARD_MAX_DEG,
                                       SERVO_RANGE_MIN_SPAN_DEG, reason, sizeof(reason)));
}

void test_validate_ranges_rejects() {
  char reason[64];
  float mins[SERVO_COUNT];
  float maxs[SERVO_COUNT];

  auto check = [&](const char* expected) {
    const char* got = validateServoRanges(mins, maxs, SERVO_HARD_MIN_DEG, SERVO_HARD_MAX_DEG,
                                          SERVO_RANGE_MIN_SPAN_DEG, reason, sizeof(reason));
    TEST_ASSERT_NOT_NULL(got);
    TEST_ASSERT_EQUAL_STRING(expected, got);
  };

  memcpy(mins, kSavedMins, sizeof(mins));
  memcpy(maxs, kSavedMaxs, sizeof(maxs));
  mins[2] = 9;
  check("left min 9 below hard min 10");

  memcpy(mins, kSavedMins, sizeof(mins));
  maxs[4] = 171;
  check("body max 171 above hard max 170");

  memcpy(maxs, kSavedMaxs, sizeof(maxs));
  mins[0] = 140;
  check("head min must be below max");

  mins[0] = 150;
  check("head min must be below max");

  memcpy(mins, kSavedMins, sizeof(mins));
  mins[1] = 125;  // span 5
  check("neck span below 6 deg");
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_hard_caps_inside_electrical_clamp);
  RUN_TEST(test_reset_soft_equals_saved_inside_hard);
  RUN_TEST(test_reset_keeps_saved_past_hard_caps);
  RUN_TEST(test_clamp);
  RUN_TEST(test_bump_narrows_max_two_degrees_inside);
  RUN_TEST(test_bump_narrows_min_two_degrees_inside);
  RUN_TEST(test_bump_never_past_saved_mid);
  RUN_TEST(test_bump_never_widens);
  RUN_TEST(test_backoff_toward_mid);
  RUN_TEST(test_backoff_retreats_from_obstacle_on_return_moves);
  RUN_TEST(test_travel_dir);
  RUN_TEST(test_dir_names);
  RUN_TEST(test_validate_ranges_ok);
  RUN_TEST(test_validate_ranges_rejects);
  return UNITY_END();
}
