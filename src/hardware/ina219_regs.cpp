#include "hardware/ina219_regs.h"

float ina219ShuntRawToMa(int16_t raw) {
  // uV / ohm = uA; / 1000 = mA.
  return static_cast<float>(raw) * INA219_SHUNT_LSB_UV / INA219_SHUNT_OHMS / 1000.0f;
}

void Ina219Health::onRead(bool ok, uint32_t nowMs) {
  lastTryMs_ = nowMs;

  if (ok) {
    failures_ = 0;
    healthy_ = true;
    return;
  }

  if (failures_ < 255) {
    failures_++;
  }
  if (failures_ >= INA219_FAILS_BEFORE_UNHEALTHY) {
    healthy_ = false;
  }
}

bool Ina219Health::shouldTry(uint32_t nowMs) const {
  return healthy_ || static_cast<uint32_t>(nowMs - lastTryMs_) >= INA219_RETRY_MS;
}
