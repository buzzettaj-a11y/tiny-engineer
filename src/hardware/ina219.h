#pragma once

#include <cstdint>

// Optional INA219 on the shared I2C bus (INA219_ADDRESS), in series with the
// PCA9685 V+ servo rail. Absent at boot = current sensing and the bump reflex
// stay off; everything else behaves like stock. Loop task only (Wire).

// Probe and configure. Call after Wire.begin(). Safe to call without the part.
bool initIna219();
// Found at boot.
bool ina219Present();
// Present and reading: false after INA219_FAILS_BEFORE_UNHEALTHY failed reads
// in a row, true again after one good read (retried once a second).
bool ina219Healthy();
// Servo-rail current in mA. False on an I2C error (the last good value stays).
bool ina219ReadCurrentMa(float& outMa);
