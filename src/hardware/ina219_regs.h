#pragma once

#include <cstdint>

// TI INA219 registers and conversions for the servo-rail current sensor.
// Raw register access (no library). Pure, host-tested.
//
// Assumes a 0.1 ohm shunt (stock breakouts, R100). PGA /8 = +/-320 mV full scale
// = +/-3.2 A. Firmware reads the shunt-voltage register (LSB 10 uV), which needs
// no calibration and survives an INA219 power-on reset; the calibration register
// is still written so the current register reads the same scale (0.1 mA/LSB).

constexpr uint8_t INA219_REG_CONFIG = 0x00;
constexpr uint8_t INA219_REG_SHUNT_VOLTAGE = 0x01;
constexpr uint8_t INA219_REG_BUS_VOLTAGE = 0x02;
constexpr uint8_t INA219_REG_CURRENT = 0x04;
constexpr uint8_t INA219_REG_CALIBRATION = 0x05;

constexpr float INA219_SHUNT_OHMS = 0.1f;
constexpr float INA219_SHUNT_LSB_UV = 10.0f;
constexpr float INA219_CURRENT_LSB_MA = 0.1f;

// BRNG=0 (16 V bus), PG=/8 (320 mV), BADC=12-bit, SADC=12-bit x8 averaged
// (4.26 ms per result), MODE=shunt continuous.
constexpr uint16_t INA219_CONFIG_VALUE =
  (0u << 13) |     // BRNG 16 V
  (3u << 11) |     // PG /8, +/-320 mV
  (0x3u << 7) |    // BADC 12-bit (unused in shunt-only mode)
  (0xBu << 3) |    // SADC 12-bit, 8 samples averaged
  0x5u;            // shunt voltage, continuous

// Datasheet: Cal = trunc(0.04096 / (Current_LSB[A] * Rshunt[ohm])).
constexpr uint16_t INA219_CALIBRATION_VALUE = 4096;

// Signed shunt-voltage register to milliamps through INA219_SHUNT_OHMS.
float ina219ShuntRawToMa(int16_t raw);

// Big-endian register bytes as the INA219 sends them.
inline uint16_t ina219Word(uint8_t msb, uint8_t lsb) {
  return static_cast<uint16_t>((static_cast<uint16_t>(msb) << 8) | lsb);
}
