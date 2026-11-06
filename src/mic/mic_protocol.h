#pragma once

#include <cstddef>
#include <cstdint>

// Robot -> brain mic stream, wire format v1 (chip/PROTOCOL.md section 3).
// Pure logic, no Arduino dependencies (host-tested).

constexpr uint8_t kMicMagic[4] = {'C', 'H', 'P', '1'};
constexpr size_t kMicHeaderLen = 16;
constexpr size_t kMicSamplesPerPacket = 256;
constexpr size_t kMicPacketMaxLen = kMicHeaderLen + kMicSamplesPerPacket * sizeof(int16_t);

constexpr uint16_t kMicFlagMuted = 1u << 0;
constexpr uint16_t kMicFlagSpeaker = 1u << 1;
constexpr uint16_t kMicFlagMoving = 1u << 2;
constexpr uint16_t kMicFlagAsleep = 1u << 3;

// One n = 0 packet per second while muted.
constexpr uint32_t kMicHeartbeatMs = 1000;
// `speaker` stays set this long after the last speaker write ends.
constexpr uint32_t kMicSpeakerTailMs = 300;
// Non-constant blocks needed before the mic counts as present.
constexpr uint8_t kMicPresentBlocks = 3;
// Button state must hold this long to count.
constexpr uint32_t kMicButtonDebounceMs = 30;

uint16_t micFlagBits(bool muted, bool speaker, bool moving, bool asleep);

// Writes the 16-byte header (little-endian fields) and returns kMicHeaderLen.
size_t micWriteHeader(
  uint8_t* out,
  uint32_t seq,
  uint32_t tMs,
  uint16_t flags,
  uint16_t sampleCount
);

// saturate(slot >> shift): arithmetic shift of the raw 32-bit I2S slot
// (24-bit INMP441 data, MSB aligned), clamped to int16.
int16_t micSampleFromSlot(int32_t slot, uint8_t shift);

// Left slot of each stereo frame (`stereo` holds frames * 2 slots) to int16.
void micConvertLeftSlots(
  const int32_t* stereo,
  size_t frames,
  uint8_t shift,
  int16_t* out
);

// Samples as int16 little-endian bytes (n * 2 bytes).
void micWriteSamplesLe(uint8_t* out, const int16_t* samples, size_t count);

// AC RMS of a block (mean removed) relative to full scale, in 0..1000.
uint16_t micBlockLevelPermille(const int16_t* samples, size_t count);

// Millis of the first sample of a block of `frames` that finished at `nowMs`.
uint32_t micBlockStartMs(uint32_t nowMs, size_t frames, uint32_t sampleRate);

// True while speaker audio is playing or ended less than tailMs ago.
bool micSpeakerRecent(bool everWritten, uint32_t lastWriteMs, uint32_t nowMs, uint32_t tailMs);

// "Mic data seen since boot": the left slot changed within a block on at least
// kMicPresentBlocks blocks. A missing mic reads a constant (pulled-down) line.
class MicPresence {
public:
  void feed(const int32_t* stereo, size_t frames);
  bool present() const { return blocks_ >= kMicPresentBlocks; }

private:
  uint8_t blocks_ = 0;
};

// Smoothed level in 0..1000 (exponential average over blocks).
class MicLevel {
public:
  void update(uint16_t blockPermille);
  void reset() { value_ = 0; }
  uint16_t permille() const { return value_; }

private:
  uint16_t value_ = 0;
};

// Mute state shared between tasks without read-modify-write atomics (the
// ESP32-C3 has none). The button task only counts presses; the loop task is the
// only writer of the packed state = (presses applied << 1) | muted. Any task
// gets the effective mute, pending presses included, from one load of each.
uint32_t micPackMuteState(uint32_t appliedPresses, bool muted);
bool micStateMuted(uint32_t state);
uint32_t micStateApplied(uint32_t state);
bool micEffectiveMuted(uint32_t state, uint32_t presses);

// Mute input mode (setting `mute_switch`, docs/adr/0004):
//  button (false): momentary button, each press toggles; state + presses as above.
//  switch (true):  maintained switch, its debounced level IS the mute state:
//                  closed to GND = listening, open (or a broken wire) = muted.
bool micEffectiveMutedMode(bool switchMode, bool switchClosed, uint32_t state, uint32_t presses);

// Button-mode state to adopt when switch mode is turned off: keeps the mute the
// switch gave (open = muted) and settles every press counted so far.
uint32_t micStateLeavingSwitch(bool switchClosed, uint32_t presses);

// Debounced momentary button or maintained switch. update() returns true once
// per press (closing); pressed() is the debounced level (true = closed).
class MicButton {
public:
  bool update(bool rawPressed, uint32_t nowMs);
  bool pressed() const { return stable_; }

private:
  bool stable_ = false;
  bool candidate_ = false;
  uint32_t candidateSinceMs_ = 0;
};
