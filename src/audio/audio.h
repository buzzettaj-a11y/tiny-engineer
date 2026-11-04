#pragma once

#include <ESP_I2S.h>

extern I2SClass I2S;

// Full-duplex I2S: speaker TX + optional INMP441 RX, 32-bit stereo slots at
// SAMPLE_RATE. False when the driver refuses the configuration.
bool initI2sBus();
bool initAudioStorage();
// Volume-scaled, each 16-bit sample in the top half of both 32-bit I2S slots.
// Blocks until the DMA buffer accepts it.
void writeMonoToSpeaker(const int16_t* samples, size_t count);
void playTone(
  float frequency,
  int durationMs
);

void playSilence(int durationMs);
void stopAllWavPlayback();
bool startBellPlayback();
bool updateBellPlayback();
void stopBellPlayback();
bool playBell();
bool startWelcomePlayback();
bool updateWelcomePlayback();
void stopWelcomePlayback();
bool playWelcome();
bool startAttentionPlayback();
bool updateAttentionPlayback();
void stopAttentionPlayback();
bool playAttention();
bool startErrorPlayback();
bool updateErrorPlayback();
void stopErrorPlayback();
bool playError();
bool startAbortPlayback();
bool updateAbortPlayback();
void stopAbortPlayback();
bool playAbort();
bool startDeadPlayback();
bool updateDeadPlayback();
void stopDeadPlayback();
bool playDead();
void runSoundTest();
