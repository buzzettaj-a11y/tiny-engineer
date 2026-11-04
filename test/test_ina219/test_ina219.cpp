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

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_address_does_not_clash);
  RUN_TEST(test_config_bits);
  RUN_TEST(test_calibration_for_0p1_ohm_and_0p1_ma_lsb);
  RUN_TEST(test_shunt_raw_to_ma);
  RUN_TEST(test_word_is_big_endian);
  return UNITY_END();
}
