#pragma once

#include <cstdint>

// Robot state the mic task reads for its packet flags (chip/PROTOCOL.md §3).
// Writers run on the Arduino loop task; the mic task only reads. Every field is
// an atomic, so no locks. Blocking loop-task work (tests, /play, explore) still
// stamps speaker writes and servo pulses as they happen.

// Every speaker write (WAV, /play, tones). `speaker` stays set for
// kMicSpeakerTailMs after the DMA queue drains.
void activityNoteSpeakerWrite(uint32_t nowMs);
// Every PCA9685 pulse write.
void activityNoteServoWrite(uint32_t nowMs);
// Once per tickRobot().
void activityPublish(bool anyServoMoving, bool asleep, bool wifiUp);
void activitySetExploring(bool exploring);

bool activitySpeaker(uint32_t nowMs);
bool activityMoving(uint32_t nowMs);
bool activityAsleep();
bool activityWifiUp();
