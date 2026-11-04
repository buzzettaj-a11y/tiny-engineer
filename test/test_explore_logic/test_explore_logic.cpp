#include <unity.h>

#include <cmath>
#include <limits>

#include "motion/explore_logic.h"
#include "servos.h"

void test_excursion_cap_toward_max() {
  // Saved max 120: sweep stops 30 degrees out, short of the 170 hard cap.
  const ExploreBound b = exploreSweepTarget(120.0f, SERVO_HARD_MAX_DEG[0], BumpDir::Max, EXPLORE_MAX_EXCURSION_DEG);
  TEST_ASSERT_EQUAL_FLOAT(150.0f, b.target);
  TEST_ASSERT_TRUE(b.excursionCapped);
}

void test_excursion_cap_toward_min() {
  const ExploreBound b = exploreSweepTarget(60.0f, SERVO_HARD_MIN_DEG[0], BumpDir::Min, EXPLORE_MAX_EXCURSION_DEG);
  TEST_ASSERT_EQUAL_FLOAT(30.0f, b.target);
  TEST_ASSERT_TRUE(b.excursionCapped);
}

void test_hard_cap_wins_when_closer() {
  const ExploreBound hi = exploreSweepTarget(150.0f, 170.0f, BumpDir::Max, EXPLORE_MAX_EXCURSION_DEG);
  TEST_ASSERT_EQUAL_FLOAT(170.0f, hi.target);
  TEST_ASSERT_FALSE(hi.excursionCapped);
  const ExploreBound lo = exploreSweepTarget(25.0f, 10.0f, BumpDir::Min, EXPLORE_MAX_EXCURSION_DEG);
  TEST_ASSERT_EQUAL_FLOAT(10.0f, lo.target);
  TEST_ASSERT_FALSE(lo.excursionCapped);
  // Exactly 30 degrees away: the hard cap, not "max_travel".
  const ExploreBound eq = exploreSweepTarget(140.0f, 170.0f, BumpDir::Max, EXPLORE_MAX_EXCURSION_DEG);
  TEST_ASSERT_EQUAL_FLOAT(170.0f, eq.target);
  TEST_ASSERT_FALSE(eq.excursionCapped);
}

void test_worst_case_sweep_fits_time_limit() {
  // Slowest speed over the longest allowed excursion.
  const float sweepMs = EXPLORE_MAX_EXCURSION_DEG / 5.0f * 1000.0f;
  TEST_ASSERT_TRUE(sweepMs < EXPLORE_TIME_LIMIT_MS);
  TEST_ASSERT_TRUE(EXPLORE_TIME_LIMIT_MS <= 12000u);
}

void test_threshold_capped() {
  TEST_ASSERT_EQUAL_UINT16(EXPLORE_THRESHOLD_CAP_MA, exploreThresholdMa(650));
  TEST_ASSERT_EQUAL_UINT16(EXPLORE_THRESHOLD_CAP_MA, exploreThresholdMa(3000));
  TEST_ASSERT_EQUAL_UINT16(EXPLORE_THRESHOLD_CAP_MA, exploreThresholdMa(0));
  TEST_ASSERT_EQUAL_UINT16(250, exploreThresholdMa(250));
  TEST_ASSERT_EQUAL_UINT16(400, EXPLORE_THRESHOLD_CAP_MA);
}

void test_baseline_plausibility() {
  TEST_ASSERT_TRUE(exploreBaselinePlausible(60.0f));
  TEST_ASSERT_TRUE(exploreBaselinePlausible(EXPLORE_MIN_BASELINE_MA));
  // Swapped VIN+/VIN-: negative; shunt not in the servo lead: ~0.
  TEST_ASSERT_FALSE(exploreBaselinePlausible(-80.0f));
  TEST_ASSERT_FALSE(exploreBaselinePlausible(0.3f));
  TEST_ASSERT_FALSE(exploreBaselinePlausible(3000.0f));
  TEST_ASSERT_FALSE(exploreBaselinePlausible(std::numeric_limits<float>::quiet_NaN()));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_excursion_cap_toward_max);
  RUN_TEST(test_excursion_cap_toward_min);
  RUN_TEST(test_hard_cap_wins_when_closer);
  RUN_TEST(test_worst_case_sweep_fits_time_limit);
  RUN_TEST(test_threshold_capped);
  RUN_TEST(test_baseline_plausibility);
  return UNITY_END();
}
