#include "mic/mic_protocol.h"

#include <cmath>
#include <cstring>

namespace {

void putLe16(uint8_t* out, uint16_t value) {
  out[0] = static_cast<uint8_t>(value & 0xFF);
  out[1] = static_cast<uint8_t>(value >> 8);
}

void putLe32(uint8_t* out, uint32_t value) {
  out[0] = static_cast<uint8_t>(value & 0xFF);
  out[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
  out[2] = static_cast<uint8_t>((value >> 16) & 0xFF);
  out[3] = static_cast<uint8_t>(value >> 24);
}

}  // namespace

uint16_t micFlagBits(bool muted, bool speaker, bool moving, bool asleep) {
  uint16_t bits = 0;
  if (muted) {
    bits |= kMicFlagMuted;
  }
  if (speaker) {
    bits |= kMicFlagSpeaker;
  }
  if (moving) {
    bits |= kMicFlagMoving;
  }
  if (asleep) {
    bits |= kMicFlagAsleep;
  }
  return bits;
}

size_t micWriteHeader(
  uint8_t* out,
  uint32_t seq,
  uint32_t tMs,
  uint16_t flags,
  uint16_t sampleCount
) {
  memcpy(out, kMicMagic, sizeof(kMicMagic));
  putLe32(out + 4, seq);
  putLe32(out + 8, tMs);
  putLe16(out + 12, flags);
  putLe16(out + 14, sampleCount);
  return kMicHeaderLen;
}

int16_t micSampleFromSlot(int32_t slot, uint8_t shift) {
  if (shift > 31) {
    shift = 31;
  }

  // >> on a negative int32 is an arithmetic shift on GCC (and defined since C++20).
  const int32_t value = slot >> shift;

  if (value > 32767) {
    return 32767;
  }
  if (value < -32768) {
    return -32768;
  }
  return static_cast<int16_t>(value);
}

void micConvertLeftSlots(
  const int32_t* stereo,
  size_t frames,
  uint8_t shift,
  int16_t* out
) {
  for (size_t i = 0; i < frames; i++) {
    out[i] = micSampleFromSlot(stereo[i * 2], shift);
  }
}

void micWriteSamplesLe(uint8_t* out, const int16_t* samples, size_t count) {
  for (size_t i = 0; i < count; i++) {
    putLe16(out + i * 2, static_cast<uint16_t>(samples[i]));
  }
}

uint16_t micBlockLevelPermille(const int16_t* samples, size_t count) {
  if (count == 0) {
    return 0;
  }

  int64_t sum = 0;
  int64_t sumSquares = 0;

  for (size_t i = 0; i < count; i++) {
    sum += samples[i];
    sumSquares += static_cast<int64_t>(samples[i]) * samples[i];
  }

  const double n = static_cast<double>(count);
  const double mean = static_cast<double>(sum) / n;
  double variance = static_cast<double>(sumSquares) / n - mean * mean;

  if (variance < 0.0) {
    variance = 0.0;
  }

  const double level = std::sqrt(variance) / 32768.0;
  const double permille = level * 1000.0 + 0.5;
  return permille >= 1000.0 ? 1000 : static_cast<uint16_t>(permille);
}

uint32_t micBlockStartMs(uint32_t nowMs, size_t frames, uint32_t sampleRate) {
  if (sampleRate == 0) {
    return nowMs;
  }

  const uint32_t spanMs =
    static_cast<uint32_t>((static_cast<uint64_t>(frames) * 1000u) / sampleRate);
  return nowMs - spanMs;
}

bool micSpeakerRecent(bool everWritten, uint32_t lastWriteMs, uint32_t nowMs, uint32_t tailMs) {
  if (!everWritten) {
    return false;
  }

  return static_cast<uint32_t>(nowMs - lastWriteMs) < tailMs;
}

uint32_t micPackMuteState(uint32_t appliedPresses, bool muted) {
  return ((appliedPresses & 0x7FFFFFFFu) << 1) | (muted ? 1u : 0u);
}

bool micStateMuted(uint32_t state) {
  return (state & 1u) != 0;
}

uint32_t micStateApplied(uint32_t state) {
  return state >> 1;
}

bool micEffectiveMuted(uint32_t state, uint32_t presses) {
  // Each pending press toggles once; only the parity matters (31-bit wrap is even).
  const uint32_t pending = ((presses & 0x7FFFFFFFu) - micStateApplied(state)) & 1u;
  return micStateMuted(state) != (pending != 0);
}

void MicPresence::feed(const int32_t* stereo, size_t frames) {
  if (present() || frames < 2) {
    return;
  }

  const int32_t first = stereo[0];

  for (size_t i = 1; i < frames; i++) {
    if (stereo[i * 2] != first) {
      blocks_++;
      return;
    }
  }
}

void MicLevel::update(uint16_t blockPermille) {
  // 0.3 * new + 0.7 * old: settles in a handful of 11.6 ms blocks.
  value_ = static_cast<uint16_t>((static_cast<uint32_t>(blockPermille) * 3u +
                                  static_cast<uint32_t>(value_) * 7u + 5u) / 10u);
}

bool MicButton::update(bool rawPressed, uint32_t nowMs) {
  if (rawPressed != candidate_) {
    candidate_ = rawPressed;
    candidateSinceMs_ = nowMs;
    return false;
  }

  if (candidate_ == stable_) {
    return false;
  }

  if (static_cast<uint32_t>(nowMs - candidateSinceMs_) < kMicButtonDebounceMs) {
    return false;
  }

  stable_ = candidate_;
  return stable_;
}
