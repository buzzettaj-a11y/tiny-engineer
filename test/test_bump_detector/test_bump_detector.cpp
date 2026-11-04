#include <unity.h>

#include <cstring>

#include "motion/bump_detector.h"

namespace {

constexpr uint16_t kThreshold = 650;

// Feeds `ms` of samples every `stepMs`; returns how many bumps fired.
int feed(BumpDetector& d, uint32_t& t, uint32_t ms, uint32_t stepMs, float ma, bool moving,
         uint16_t threshold = kThreshold) {
  int bumps = 0;
  for (uint32_t end = t + ms; t < end; t += stepMs) {
    bumps += d.update(t, ma, moving, threshold) ? 1 : 0;
  }
  return bumps;
}

}  // namespace

void test_baseline_tracks_idle_current() {
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 2000, 10, 120.0f, false);
  TEST_ASSERT_TRUE(d.baselineValid());
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 120.0f, d.baselineMa());
}

void test_baseline_frozen_while_moving() {
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 2000, 10, 100.0f, false);
  feed(d, t, 2000, 10, 600.0f, true);  // below threshold: normal motion current
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 100.0f, d.baselineMa());
}

void test_bump_needs_60ms_over_threshold_while_moving() {
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 1000, 10, 100.0f, false);

  // 50 ms over threshold: no bump.
  TEST_ASSERT_EQUAL(0, feed(d, t, 50, 10, 900.0f, true));
  // Drops back: window resets.
  TEST_ASSERT_EQUAL(0, feed(d, t, 20, 10, 300.0f, true));
  // 50 ms again: still no bump.
  TEST_ASSERT_EQUAL(0, feed(d, t, 50, 10, 900.0f, true));
  TEST_ASSERT_EQUAL(0, feed(d, t, 20, 10, 300.0f, true));

  // Sustained: fires once at >= 60 ms.
  uint32_t fireAt = 0;
  const uint32_t start = t;
  for (; t < start + 200; t += 10) {
    if (d.update(t, 900.0f + (t - start), true, kThreshold)) {
      fireAt = t;
      break;
    }
  }
  TEST_ASSERT_EQUAL_UINT32(start + 60, fireAt);
  TEST_ASSERT_FLOAT_WITHIN(0.5f, 960.0f, d.bumpPeakMa());
}

void test_threshold_is_above_baseline() {
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 3000, 10, 400.0f, false);
  // 400 + 650 = 1050: 1000 mA is not a bump with this baseline.
  TEST_ASSERT_EQUAL(0, feed(d, t, 500, 10, 1000.0f, true));
  TEST_ASSERT_EQUAL(1, feed(d, t, 100, 10, 1100.0f, true));
}

void test_no_bump_when_still() {
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 500, 10, 100.0f, false);
  TEST_ASSERT_EQUAL(0, feed(d, t, 500, 10, 2000.0f, false));
}

void test_threshold_zero_disables() {
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 500, 10, 100.0f, false, 0);
  TEST_ASSERT_EQUAL(0, feed(d, t, 1000, 10, 3000.0f, true, 0));
}

void test_cooldown_500ms() {
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 500, 10, 100.0f, false);
  TEST_ASSERT_EQUAL(1, feed(d, t, 70, 10, 1500.0f, true));
  // Still stalled: ignored for 500 ms after the bump.
  const uint32_t bumpAt = t - 10;
  TEST_ASSERT_EQUAL(0, feed(d, t, (bumpAt + 500) - t, 10, 1500.0f, true));
  // Then a new 60 ms window can fire again.
  TEST_ASSERT_EQUAL(1, feed(d, t, 80, 10, 1500.0f, true));
}

void test_slow_tick_rate_still_detects() {
  // Loop ticks can be ~20 ms apart while the OLED redraws.
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 500, 20, 100.0f, false);
  TEST_ASSERT_EQUAL(1, feed(d, t, 100, 20, 1200.0f, true));
}

void test_reset() {
  BumpDetector d;
  uint32_t t = 0;
  feed(d, t, 500, 10, 100.0f, false);
  d.reset();
  TEST_ASSERT_FALSE(d.baselineValid());
}

void test_log_newest_first_and_wraps() {
  BumpLog log;
  TEST_ASSERT_EQUAL(0, log.size());

  for (uint32_t i = 0; i < BUMP_LOG_CAPACITY + 5; i++) {
    log.push(BumpLog::make(i, static_cast<int>(i % 5), 100.0f + i, BumpDir::Max, 700.0f, "typing"));
  }

  TEST_ASSERT_EQUAL(BUMP_LOG_CAPACITY, log.size());
  TEST_ASSERT_EQUAL_UINT32(BUMP_LOG_CAPACITY + 4, log.at(0).tMs);
  TEST_ASSERT_EQUAL_UINT32(BUMP_LOG_CAPACITY + 3, log.at(1).tMs);
  TEST_ASSERT_EQUAL_UINT32(5, log.at(BUMP_LOG_CAPACITY - 1).tMs);

  log.clear();
  TEST_ASSERT_EQUAL(0, log.size());
  log.push(BumpLog::make(77, 2, 33.5f, BumpDir::Min, 812.0f, "none"));
  TEST_ASSERT_EQUAL(1, log.size());
  TEST_ASSERT_EQUAL_UINT32(77, log.at(0).tMs);
  TEST_ASSERT_EQUAL_UINT8(2, log.at(0).joint);
  TEST_ASSERT_EQUAL_FLOAT(33.5f, log.at(0).angle);
  TEST_ASSERT_TRUE(log.at(0).dir == BumpDir::Min);
  TEST_ASSERT_EQUAL_FLOAT(812.0f, log.at(0).currentMa);
  TEST_ASSERT_EQUAL_STRING("none", log.at(0).anim);
}

void test_log_entry_name_truncates() {
  const BumpEntry e = BumpLog::make(1, 0, 0, BumpDir::Max, 0, "a-very-long-animation-name");
  TEST_ASSERT_EQUAL(BUMP_ANIM_NAME_LEN, strlen(e.anim));
  const BumpEntry n = BumpLog::make(1, 0, 0, BumpDir::Max, 0, nullptr);
  TEST_ASSERT_EQUAL_STRING("", n.anim);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_baseline_tracks_idle_current);
  RUN_TEST(test_baseline_frozen_while_moving);
  RUN_TEST(test_bump_needs_60ms_over_threshold_while_moving);
  RUN_TEST(test_threshold_is_above_baseline);
  RUN_TEST(test_no_bump_when_still);
  RUN_TEST(test_threshold_zero_disables);
  RUN_TEST(test_cooldown_500ms);
  RUN_TEST(test_slow_tick_rate_still_detects);
  RUN_TEST(test_reset);
  RUN_TEST(test_log_newest_first_and_wraps);
  RUN_TEST(test_log_entry_name_truncates);
  return UNITY_END();
}
