#pragma once

#include <cstddef>
#include <cstdint>

// The I2S bus runs 32-bit stereo slots so the INMP441 mic gets its 64 BCLK per
// frame (full duplex with the MAX98357A). Speaker samples stay 16-bit: each one
// sits in the top half of its 32-bit slot. No Arduino dependencies (host-tested).

// One sample scaled by `volumePercent` (0..100) and saturated to int16.
int16_t i2sScaleVolume(int16_t sample, uint8_t volumePercent);

// 16-bit sample placed in the top half of a 32-bit I2S slot.
constexpr int32_t i2sSlotFromSample(int16_t sample) {
  return static_cast<int32_t>(static_cast<uint32_t>(static_cast<uint16_t>(sample)) << 16);
}

// Writes `count` stereo frames (left, right) to `out`, which must hold
// count * 2 slots. Every mono sample is volume-scaled and duplicated to both slots.
void i2sPackMonoToStereo32(
  const int16_t* samples,
  size_t count,
  uint8_t volumePercent,
  int32_t* out
);
