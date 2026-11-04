#include <unity.h>

#include <cstdint>

#include "audio/i2s_frame.h"

void test_slot_puts_sample_in_top_half() {
  TEST_ASSERT_EQUAL_HEX32(0x12340000u, static_cast<uint32_t>(i2sSlotFromSample(0x1234)));
  TEST_ASSERT_EQUAL_HEX32(0xFFFF0000u, static_cast<uint32_t>(i2sSlotFromSample(-1)));
  TEST_ASSERT_EQUAL_INT32(-32768 * 65536, i2sSlotFromSample(-32768));
  TEST_ASSERT_EQUAL_INT32(32767 * 65536, i2sSlotFromSample(32767));
  TEST_ASSERT_EQUAL_INT32(0, i2sSlotFromSample(0));
}

void test_slot_keeps_sign_and_order() {
  // The 32-bit slot read as a signed value is the 16-bit sample scaled by 2^16.
  for (int v = -32768; v <= 32767; v += 997) {
    TEST_ASSERT_EQUAL_INT32(v * 65536, i2sSlotFromSample(static_cast<int16_t>(v)));
  }
}

void test_volume_scales_and_saturates() {
  TEST_ASSERT_EQUAL_INT16(1000, i2sScaleVolume(1000, 100));
  TEST_ASSERT_EQUAL_INT16(700, i2sScaleVolume(1000, 70));
  TEST_ASSERT_EQUAL_INT16(0, i2sScaleVolume(1000, 0));
  TEST_ASSERT_EQUAL_INT16(-22937, i2sScaleVolume(-32768, 70));
  TEST_ASSERT_EQUAL_INT16(32767, i2sScaleVolume(32767, 100));
  TEST_ASSERT_EQUAL_INT16(-32768, i2sScaleVolume(-32768, 100));
}

void test_pack_duplicates_scaled_sample_to_both_slots() {
  const int16_t mono[3] = {1000, -2000, 32767};
  int32_t out[6] = {};

  i2sPackMonoToStereo32(mono, 3, 50, out);

  TEST_ASSERT_EQUAL_INT32(500 * 65536, out[0]);
  TEST_ASSERT_EQUAL_INT32(500 * 65536, out[1]);
  TEST_ASSERT_EQUAL_INT32(-1000 * 65536, out[2]);
  TEST_ASSERT_EQUAL_INT32(-1000 * 65536, out[3]);
  TEST_ASSERT_EQUAL_INT32(16383 * 65536, out[4]);
  TEST_ASSERT_EQUAL_INT32(16383 * 65536, out[5]);
}

void test_pack_zero_count_writes_nothing() {
  int32_t out[2] = {7, 7};
  i2sPackMonoToStereo32(nullptr, 0, 70, out);
  TEST_ASSERT_EQUAL_INT32(7, out[0]);
  TEST_ASSERT_EQUAL_INT32(7, out[1]);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_slot_puts_sample_in_top_half);
  RUN_TEST(test_slot_keeps_sign_and_order);
  RUN_TEST(test_volume_scales_and_saturates);
  RUN_TEST(test_pack_duplicates_scaled_sample_to_both_slots);
  RUN_TEST(test_pack_zero_count_writes_nothing);
  return UNITY_END();
}
