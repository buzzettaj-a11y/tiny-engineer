#include <unity.h>

#include "hardware/ina219_regs.h"
#include "pins.h"

void test_address_does_not_clash() {
  TEST_ASSERT_EQUAL_HEX8(0x41, INA219_ADDRESS);
  TEST_ASSERT_NOT_EQUAL(PCA9685_ADDRESS, INA219_ADDRESS);
  TEST_ASSERT_NOT_EQUAL(OLED_ADDRESS, INA219_ADDRESS);
}

void test_config_bits() {
  // RST=0, BRNG=0 (16 V), PG=11 (/8, 320 mV)
  TEST_ASSERT_EQUAL_HEX16(0x0000, INA219_CONFIG_VALUE & 0x8000);
  TEST_ASSERT_EQUAL_HEX16(0x0000, INA219_CONFIG_VALUE & 0x2000);
  TEST_ASSERT_EQUAL_HEX16(0x1800, INA219_CONFIG_VALUE & 0x1800);
  // SADC = 1011 (12-bit, 8 samples, 4.26 ms)
  TEST_ASSERT_EQUAL_HEX16(0xB, (INA219_CONFIG_VALUE >> 3) & 0xF);
  // MODE = 101 (shunt voltage, continuous)
  TEST_ASSERT_EQUAL_HEX16(0x5, INA219_CONFIG_VALUE & 0x7);
  TEST_ASSERT_EQUAL_HEX16(0x19DD, INA219_CONFIG_VALUE);
}

void test_calibration_for_0p1_ohm_and_0p1_ma_lsb() {
  // Cal = trunc(0.04096 / (0.0001 A * 0.1 ohm)) = 4096
  TEST_ASSERT_EQUAL_UINT16(4096, INA219_CALIBRATION_VALUE);
}

void test_shunt_raw_to_ma() {
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, ina219ShuntRawToMa(0));
  // 10 uV / 0.1 ohm = 0.1 mA per LSB.
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.1f, ina219ShuntRawToMa(1));
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 650.0f, ina219ShuntRawToMa(6500));
  // Full scale +/-320 mV = +/-3.2 A.
  TEST_ASSERT_FLOAT_WITHIN(0.1f, 3200.0f, ina219ShuntRawToMa(32000));
  TEST_ASSERT_FLOAT_WITHIN(0.1f, -1000.0f, ina219ShuntRawToMa(-10000));
}

void test_word_is_big_endian() {
  TEST_ASSERT_EQUAL_HEX16(0x19DD, ina219Word(0x19, 0xDD));
  TEST_ASSERT_EQUAL_INT16(-1, static_cast<int16_t>(ina219Word(0xFF, 0xFF)));
}

void test_health_lost_after_consecutive_failures_and_recovers() {
  Ina219Health h;
  TEST_ASSERT_TRUE(h.healthy());
  uint32_t t = 0;

  for (uint8_t i = 0; i + 1 < INA219_FAILS_BEFORE_UNHEALTHY; i++) {
    h.onRead(false, t += 10);
    TEST_ASSERT_TRUE(h.healthy());
  }
  // A good read in between resets the count.
  h.onRead(true, t += 10);
  for (uint8_t i = 0; i + 1 < INA219_FAILS_BEFORE_UNHEALTHY; i++) {
    h.onRead(false, t += 10);
  }
  TEST_ASSERT_TRUE(h.healthy());
  h.onRead(false, t += 10);
  TEST_ASSERT_FALSE(h.healthy());

  // While lost: retried once a second, not every poll.
  TEST_ASSERT_FALSE(h.shouldTry(t + 10));
  TEST_ASSERT_FALSE(h.shouldTry(t + INA219_RETRY_MS - 1));
  TEST_ASSERT_TRUE(h.shouldTry(t + INA219_RETRY_MS));

  // One good read recovers.
  h.onRead(true, t + INA219_RETRY_MS);
  TEST_ASSERT_TRUE(h.healthy());
  TEST_ASSERT_TRUE(h.shouldTry(t + INA219_RETRY_MS + 5));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_address_does_not_clash);
  RUN_TEST(test_config_bits);
  RUN_TEST(test_calibration_for_0p1_ohm_and_0p1_ma_lsb);
  RUN_TEST(test_shunt_raw_to_ma);
  RUN_TEST(test_word_is_big_endian);
  RUN_TEST(test_health_lost_after_consecutive_failures_and_recovers);
  return UNITY_END();
}
