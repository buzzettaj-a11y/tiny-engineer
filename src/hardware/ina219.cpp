#include "hardware/ina219.h"

#include <Arduino.h>
#include <Wire.h>

#include "display/oled.h"
#include "hardware/ina219_regs.h"
#include "pins.h"
#include "serial_log.h"

namespace {

// A brownout on the 5 V rail resets the INA219 to its defaults; the config is
// checked this often and rewritten when it changed.
constexpr uint32_t kConfigCheckMs = 1000;

bool g_present = false;
uint32_t g_lastConfigCheckMs = 0;

bool writeRegister(uint8_t reg, uint16_t value) {
  Wire.beginTransmission(INA219_ADDRESS);
  Wire.write(reg);
  Wire.write(static_cast<uint8_t>(value >> 8));
  Wire.write(static_cast<uint8_t>(value & 0xFF));
  return Wire.endTransmission() == 0;
}

bool readRegister(uint8_t reg, uint16_t& out) {
  Wire.beginTransmission(INA219_ADDRESS);
  Wire.write(reg);

  if (Wire.endTransmission() != 0) {
    return false;
  }

  if (Wire.requestFrom(INA219_ADDRESS, static_cast<size_t>(2)) != 2) {
    return false;
  }

  const int msb = Wire.read();
  const int lsb = Wire.read();

  if (msb < 0 || lsb < 0) {
    return false;
  }

  out = ina219Word(static_cast<uint8_t>(msb), static_cast<uint8_t>(lsb));
  return true;
}

bool configure() {
  if (!writeRegister(INA219_REG_CONFIG, INA219_CONFIG_VALUE) ||
      !writeRegister(INA219_REG_CALIBRATION, INA219_CALIBRATION_VALUE)) {
    return false;
  }

  uint16_t readBack = 0;
  return readRegister(INA219_REG_CONFIG, readBack) && readBack == INA219_CONFIG_VALUE;
}

void checkConfig(uint32_t nowMs) {
  if (nowMs - g_lastConfigCheckMs < kConfigCheckMs) {
    return;
  }

  g_lastConfigCheckMs = nowMs;
  uint16_t config = 0;

  if (readRegister(INA219_REG_CONFIG, config) && config != INA219_CONFIG_VALUE) {
    serialLogPrintln("[ina219] config lost (rail brownout?); reconfiguring");
    configure();
  }
}

}  // namespace

bool initIna219() {
  serialLogPrintln();
  serialLogPrintln("Checking INA219 at 0x41...");

  if (!i2cDeviceConnected(INA219_ADDRESS)) {
    serialLogPrintln("INA219 not found; bump reflex off");
    g_present = false;
    return false;
  }

  g_present = configure();
  g_lastConfigCheckMs = millis();

  serialLogPrintln(g_present ? "INA219 OK" : "INA219 config failed; bump reflex off");
  return g_present;
}

bool ina219Present() {
  return g_present;
}

bool ina219ReadCurrentMa(float& outMa) {
  if (!g_present) {
    return false;
  }

  checkConfig(millis());

  uint16_t raw = 0;

  if (!readRegister(INA219_REG_SHUNT_VOLTAGE, raw)) {
    return false;
  }

  outMa = ina219ShuntRawToMa(static_cast<int16_t>(raw));
  return true;
}
