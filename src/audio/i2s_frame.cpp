#include "audio/i2s_frame.h"

int16_t i2sScaleVolume(int16_t sample, uint8_t volumePercent) {
  int32_t scaled = (static_cast<int32_t>(sample) * volumePercent) / 100;

  if (scaled > 32767) {
    scaled = 32767;
  } else if (scaled < -32768) {
    scaled = -32768;
  }

  return static_cast<int16_t>(scaled);
}

void i2sPackMonoToStereo32(
  const int16_t* samples,
  size_t count,
  uint8_t volumePercent,
  int32_t* out
) {
  for (size_t i = 0; i < count; i++) {
    const int32_t slot = i2sSlotFromSample(i2sScaleVolume(samples[i], volumePercent));
    out[i * 2] = slot;
    out[i * 2 + 1] = slot;
  }
}
