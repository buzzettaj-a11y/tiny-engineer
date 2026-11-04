#include <unity.h>

#include <cstring>

#include "motion/bump_detector.h"

namespace {

constexpr uint16_t kThreshold = 650;

struct Feed {
  BumpDetector& d;
  uint32_t& t;
  bool outputsOn = true;
  uint16_t threshold = kThreshold;
  const float* angles = nullptr;

  // Feeds `ms` of samples every `stepMs`; returns the first event (None if none)
  // and stops right after it.
  BumpEvent run(uint32_t ms, uint32_t stepMs, float ma, bool motion) {
    for (uint32_t end = t + ms; t < end; t += stepMs) {
      const BumpSample s{t, ma, motion, outputsOn, threshold, angles};
      const BumpEvent e = d.update(s);
      if (e != BumpEvent::None) {
        t += stepMs;
        return e;
      }
    }
    return BumpEvent::None;
  }
};

// Learns an armed powered baseline of `ma`.
void arm(BumpDetector& d, uint32_t& t, float ma) {
  Feed f{d, t};
  f.run(500, 10, ma, false);
}

}  // namespace

void test_not_armed_until_still_samples_with_outputs_on() {
  BumpDetector d;
  uint32_t t = 0;
  Feed f{d, t};
  TEST_ASSERT_FALSE(d.armed(true));
  f.run((BUMP_ARM_STILL_SAMPLES - 1) * 10, 10, 120.0f, false);
  TEST_ASSERT_FALSE(d.armed(true));
  f.run(10, 10, 120.0f, false);
  TEST_ASSERT_TRUE(d.armed(true));
  TEST_ASSERT_FLOAT_WITHIN(0.5f, 120.0f, d.baselineMa(true));
  // The limp baseline is separate and still unknown.
  TEST_ASSERT_FALSE(d.armed(false));
}

void test_never_seeded_from_moving_samples() {
  BumpDetector d;
  uint32_t t = 0;
  Feed f{d, t};
  // Moving from boot: no baseline, so no bump (only the absolute trip guards).
  TEST_ASSERT_TRUE(f.run(1000, 10, 1500.0f, true) == BumpEvent::None);
  TEST_ASSERT_FALSE(d.armed(true));
}

void test_bump_needs_60ms_over_threshold_while_moving() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 100.0f);
  Feed f{d, t};

  TEST_ASSERT_TRUE(f.run(50, 10, 900.0f, true) == BumpEvent::None);
  TEST_ASSERT_TRUE(f.run(20, 10, 300.0f, true) == BumpEvent::None);  // window resets
  TEST_ASSERT_TRUE(f.run(50, 10, 900.0f, true) == BumpEvent::None);
  TEST_ASSERT_TRUE(f.run(20, 10, 300.0f, true) == BumpEvent::None);

  const uint32_t start = t;
  TEST_ASSERT_TRUE(f.run(200, 10, 960.0f, true) == BumpEvent::Bump);
  TEST_ASSERT_EQUAL_UINT32(start + 70, t);  // fired at +60, t advanced one step
  TEST_ASSERT_FLOAT_WITHIN(0.5f, 960.0f, d.eventPeakMa());
}

void test_threshold_is_above_baseline() {
  BumpDetector d;
  uint32_t t = 0;
  // A real holding load: 400 mA (arming averages, then the EMA follows).
  arm(d, t, 400.0f);
  Feed f{d, t};
  TEST_ASSERT_TRUE(f.run(500, 10, 1000.0f, true) == BumpEvent::None);
  TEST_ASSERT_TRUE(f.run(100, 10, 1100.0f, true) == BumpEvent::Bump);
}

void test_threshold_zero_disables() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 100.0f);
  Feed f{d, t};
  f.threshold = 0;
  TEST_ASSERT_TRUE(f.run(1000, 10, 2000.0f, true) == BumpEvent::None);
  TEST_ASSERT_TRUE(f.run(1000, 10, 2000.0f, false) == BumpEvent::None);
}

void test_absolute_trip_is_immediate() {
  BumpDetector d;
  uint32_t t = 0;
  Feed f{d, t};
  // Even unarmed.
  TEST_ASSERT_TRUE(f.run(20, 10, BUMP_ABSOLUTE_TRIP_MA, true) == BumpEvent::Bump);
}

void test_cooldown_500ms() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 100.0f);
  Feed f{d, t};
  TEST_ASSERT_TRUE(f.run(100, 10, 1500.0f, true) == BumpEvent::Bump);
  const uint32_t bumpAt = t - 10;
  TEST_ASSERT_TRUE(f.run((bumpAt + 500) - t, 10, 1500.0f, true) == BumpEvent::None);
  TEST_ASSERT_TRUE(f.run(100, 10, 1500.0f, true) == BumpEvent::Bump);
}

void test_slow_tick_rate_still_detects() {
  BumpDetector d;
  uint32_t t = 0;
  Feed f{d, t};
  f.run(500, 20, 100.0f, false);
  TEST_ASSERT_TRUE(f.run(100, 20, 1200.0f, true) == BumpEvent::Bump);
}

void test_static_stall_after_motion_ends() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 100.0f);
  Feed f{d, t};
  // Move ends against an obstacle: current stays high with no motion.
  TEST_ASSERT_TRUE(f.run(BUMP_STALL_MS - 20, 10, 900.0f, false) == BumpEvent::None);
  TEST_ASSERT_TRUE(f.run(100, 10, 900.0f, false) == BumpEvent::Stall);
  // The stall current never entered the baseline.
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 100.0f, d.baselineMa(true));
}

void test_no_stall_when_limp() {
  BumpDetector d;
  uint32_t t = 0;
  Feed f{d, t};
  f.outputsOn = false;
  f.run(500, 10, 5.0f, false);
  TEST_ASSERT_TRUE(f.run(1000, 10, 900.0f, false) == BumpEvent::None);
}

void test_baseline_frozen_while_moving_and_ignores_high_samples() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 100.0f);
  Feed f{d, t};
  f.run(2000, 10, 600.0f, true);  // motion current below threshold
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 100.0f, d.baselineMa(true));
  // Still but above baseline + threshold/2: not learned (yet).
  f.run(1500, 10, 500.0f, false);
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 100.0f, d.baselineMa(true));
  // Below baseline + threshold/2: learned.
  f.run(2000, 10, 300.0f, false);
  TEST_ASSERT_FLOAT_WITHIN(5.0f, 300.0f, d.baselineMa(true));
}

void test_new_holding_load_accepted_slowly() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 100.0f);
  Feed f{d, t};
  // 500 mA hold (between half and full threshold) for longer than 2 s: learned.
  TEST_ASSERT_TRUE(f.run(BUMP_BASELINE_SLOW_ACCEPT_MS + 3000, 10, 500.0f, false) == BumpEvent::None);
  TEST_ASSERT_FLOAT_WITHIN(20.0f, 500.0f, d.baselineMa(true));
}

void test_window_open_angles_are_reported() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 100.0f);

  float angles[SERVO_COUNT] = {90, 90, 90, 90, 90};
  bool fired = false;
  for (int i = 0; i < 20 && !fired; i++, t += 10) {
    angles[0] = 120.0f + i;  // joint 0 keeps driving into the obstacle
    const float ma = i >= 3 ? 1200.0f : 200.0f;
    const BumpSample s{t, ma, true, true, kThreshold, angles};
    fired = d.update(s) == BumpEvent::Bump;
  }

  TEST_ASSERT_TRUE(fired);
  TEST_ASSERT_TRUE(d.eventAnglesValid());
  // Window opened at i = 3 (123°); the bump fired later at a deeper angle.
  TEST_ASSERT_EQUAL_FLOAT(123.0f, d.eventAngle(0));
  TEST_ASSERT_EQUAL_FLOAT(90.0f, d.eventAngle(1));
}

void test_rearm_after_release_keeps_powered_baseline() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 250.0f);
  Feed f{d, t};
  // PWM released: limp baseline ~5 mA.
  f.outputsOn = false;
  f.run(3000, 10, 5.0f, false);
  TEST_ASSERT_TRUE(d.armed(false));
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 5.0f, d.baselineMa(false));
  // Re-energised and moving at once: judged against the powered baseline
  // (250), not the limp one (5): 700 mA is normal motion.
  f.outputsOn = true;
  TEST_ASSERT_TRUE(f.run(1000, 10, 700.0f, true) == BumpEvent::None);
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 250.0f, d.baselineMa(true));
  // 1000 mA (> 250 + 650) is still a bump.
  TEST_ASSERT_TRUE(f.run(200, 10, 1000.0f, true) == BumpEvent::Bump);
}

void test_reset() {
  BumpDetector d;
  uint32_t t = 0;
  arm(d, t, 100.0f);
  d.reset();
  TEST_ASSERT_FALSE(d.armed(true));
}

void test_tracker_slow_pose_counts_as_motion() {
  // A /pose move re-targets every tick by less than the deadband.
  MotionTracker m;
  float angles[SERVO_COUNT] = {90, 90, 90, 90, 90};
  float targets[SERVO_COUNT] = {90, 90, 90, 90, 90};
  uint32_t t = 0;
  m.observe(t, angles, targets);
  TEST_ASSERT_FALSE(m.active(t));

  for (int i = 1; i <= 50; i++) {
    t += 10;
    targets[1] = 90.0f + 0.1f * i;  // 10 deg/s, always within the deadband
    angles[1] = targets[1];
    m.observe(t, angles, targets);
    TEST_ASSERT_TRUE(m.active(t));
  }

  TEST_ASSERT_TRUE(m.jointRecent(1, t, BUMP_MOTION_TAIL_MS));
  TEST_ASSERT_FALSE(m.jointRecent(0, t, BUMP_MOTION_TAIL_MS));
  TEST_ASSERT_TRUE(m.jointDir(1) == BumpDir::Max);
}

void test_tracker_tail() {
  MotionTracker m;
  float angles[SERVO_COUNT] = {90, 90, 90, 90, 90};
  float targets[SERVO_COUNT] = {90, 90, 90, 90, 80};
  uint32_t t = 1000;
  m.observe(t, angles, targets);  // joint 4 slewing toward min
  TEST_ASSERT_TRUE(m.active(t));
  TEST_ASSERT_TRUE(m.jointDir(4) == BumpDir::Min);

  angles[4] = 80;  // arrived; nothing changes any more: the tail runs from t
  m.observe(t + 10, angles, targets);
  m.observe(t + 100, angles, targets);
  TEST_ASSERT_TRUE(m.active(t + BUMP_MOTION_TAIL_MS - 1));
  TEST_ASSERT_FALSE(m.active(t + BUMP_MOTION_TAIL_MS));
  TEST_ASSERT_TRUE(m.jointRecent(4, t + 1000, 2000));
}

void test_tracker_note_motion() {
  MotionTracker m;
  TEST_ASSERT_FALSE(m.active(0));
  m.noteMotion(500);
  TEST_ASSERT_TRUE(m.active(600));
  TEST_ASSERT_FALSE(m.active(500 + BUMP_MOTION_TAIL_MS));
  // A stamp slightly newer than `now` still counts.
  TEST_ASSERT_TRUE(m.active(490));
}

void test_tail_keeps_bump_window_open_after_move_ends() {
  // Detector + tracker: current rises as a slow move ends; the tail keeps it
  // "moving", so the 60 ms window completes instead of resetting.
  BumpDetector d;
  MotionTracker m;
  float angles[SERVO_COUNT] = {90, 90, 90, 90, 90};
  float targets[SERVO_COUNT] = {90, 90, 90, 90, 90};
  uint32_t t = 0;

  for (; t < 500; t += 10) {
    m.observe(t, angles, targets);
    d.update(BumpSample{t, 100.0f, m.active(t), true, kThreshold, angles});
  }

  targets[0] = 95;  // one short move
  m.observe(t, angles, targets);
  angles[0] = 95;
  BumpEvent e = BumpEvent::None;
  for (int i = 0; i < 12 && e == BumpEvent::None; i++, t += 10) {
    m.observe(t, angles, targets);
    e = d.update(BumpSample{t, 1000.0f, m.active(t), true, kThreshold, angles});
  }
  TEST_ASSERT_TRUE(e == BumpEvent::Bump);
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
  log.push(BumpLog::make(77, 2, 33.5f, BumpDir::Min, 812.0f, "none", true));
  TEST_ASSERT_EQUAL(1, log.size());
  TEST_ASSERT_EQUAL_UINT32(77, log.at(0).tMs);
  TEST_ASSERT_EQUAL_UINT8(2, log.at(0).joint);
  TEST_ASSERT_EQUAL_FLOAT(33.5f, log.at(0).angle);
  TEST_ASSERT_TRUE(log.at(0).dir == BumpDir::Min);
  TEST_ASSERT_EQUAL_FLOAT(812.0f, log.at(0).currentMa);
  TEST_ASSERT_TRUE(log.at(0).stall);
  TEST_ASSERT_EQUAL_STRING("none", log.at(0).anim);
}

void test_log_entry_name_truncates() {
  const BumpEntry e = BumpLog::make(1, 0, 0, BumpDir::Max, 0, "a-very-long-animation-name");
  TEST_ASSERT_EQUAL(BUMP_ANIM_NAME_LEN, strlen(e.anim));
  TEST_ASSERT_FALSE(e.stall);
  const BumpEntry n = BumpLog::make(1, 0, 0, BumpDir::Max, 0, nullptr);
  TEST_ASSERT_EQUAL_STRING("", n.anim);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_not_armed_until_still_samples_with_outputs_on);
  RUN_TEST(test_never_seeded_from_moving_samples);
  RUN_TEST(test_bump_needs_60ms_over_threshold_while_moving);
  RUN_TEST(test_threshold_is_above_baseline);
  RUN_TEST(test_threshold_zero_disables);
  RUN_TEST(test_absolute_trip_is_immediate);
  RUN_TEST(test_cooldown_500ms);
  RUN_TEST(test_slow_tick_rate_still_detects);
  RUN_TEST(test_static_stall_after_motion_ends);
  RUN_TEST(test_no_stall_when_limp);
  RUN_TEST(test_baseline_frozen_while_moving_and_ignores_high_samples);
  RUN_TEST(test_new_holding_load_accepted_slowly);
  RUN_TEST(test_window_open_angles_are_reported);
  RUN_TEST(test_rearm_after_release_keeps_powered_baseline);
  RUN_TEST(test_reset);
  RUN_TEST(test_tracker_slow_pose_counts_as_motion);
  RUN_TEST(test_tracker_tail);
  RUN_TEST(test_tracker_note_motion);
  RUN_TEST(test_tail_keeps_bump_window_open_after_move_ends);
  RUN_TEST(test_log_newest_first_and_wraps);
  RUN_TEST(test_log_entry_name_truncates);
  return UNITY_END();
}
