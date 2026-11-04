#include "hardware/ina219_regs.h"

float ina219ShuntRawToMa(int16_t raw) {
  // uV / ohm = uA; / 1000 = mA.
  return static_cast<float>(raw) * INA219_SHUNT_LSB_UV / INA219_SHUNT_OHMS / 1000.0f;
}
