#pragma once

#include <cstdint>

// INMP441 mic stream to the brain (chip/PROTOCOL.md section 3).
//
// A FreeRTOS task (not a loop() poll) reads I2S RX: loop() blocks for seconds at
// a time (/play streams, /test/*, /ranges/explore, setup moves), and the mic must
// keep reading the DMA and stamping `speaker` / `moving` through all of them.
// The task blocks in i2s_channel_read() between 11.6 ms blocks (256 frames at
// 22050 Hz), so it costs loop() almost nothing. It never logs or touches
// I2C/NVS; micTick() on the loop task does that.

// Loads the saved mute state, sets up the GP7 button and starts the task.
// Call after initI2sBus().
void initMic();

// Loop task, once per tickRobot(): pushes setting changes to the task,
// persists button toggles, refreshes the mute indicator, logs.
void micTick(uint32_t nowMs);

bool micMuted();
// Setting mute_switch: the GP7 switch level is the mute state (docs/adr/0004).
bool micMuteFollowsSwitch();
// Saves to NVS and updates the indicator. Loop task only. False (nothing
// changed) in switch mode, where the switch is the only authority.
bool micSetMuted(bool muted);

struct MicStatus {
  bool muted;
  bool switchMode;  // mute_input "switch" (else "button")
  bool streaming;
  bool present;
  float level;  // 0..1, 0 while muted
};

MicStatus micStatus();
