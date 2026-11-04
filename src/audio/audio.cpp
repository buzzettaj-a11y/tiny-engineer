#include <Arduino.h>
#include <ESP_I2S.h>
#include <FS.h>
#include <LittleFS.h>
#include <math.h>

#include <cstring>

#include "driver/gpio.h"

#include "audio/audio_cues.h"
#include "audio/i2s_frame.h"
#include "audio/wav_stream.h"
#include "hardware/activity.h"
#include "pins.h"
#include "display/oled.h"
#include "settings/settings.h"
#include "audio/audio.h"
#include "serial_log.h"

I2SClass I2S;

namespace {

bool audioStorageReady = false;

// One stereo 32-bit frame buffer shared by every speaker writer. They all run on
// the loop task and never nest, and keeping it off the stack matters inside the
// deep /play call chain (2 KB).
constexpr size_t kSpeakerFrames = 256;
int32_t g_speakerFrames[kSpeakerFrames * 2];

void writeSpeakerFrames(size_t frames) {
  I2S.write(
    reinterpret_cast<const uint8_t*>(g_speakerFrames),
    frames * 2 * sizeof(int32_t)
  );
  activityNoteSpeakerWrite(millis());
}

constexpr const char* kAudioPartition = "spiffs";

void logAudioStorageContents() {
  File root = LittleFS.open("/");

  if (!root || !root.isDirectory()) {
    serialLogPrintln("LittleFS root unavailable");
    return;
  }

  serialLogPrintln("LittleFS files:");

  File entry = root.openNextFile();

  while (entry) {
    serialLogPrint("  ");
    serialLogPrintln(entry.name());
    entry.close();
    entry = root.openNextFile();
  }

  root.close();
}

bool playWavBlocking(
  WavClip clip,
  const char* line2
) {
  const char* line1 = wavClipOledTitle(clip);

  showOledText(line1, line2);

  if (!wavStreamStart(clip)) {
    showOledText(line1, "Failed");
    return false;
  }

  while (wavStreamUpdate()) {
  }

  showOledText(line1, "OK");
  return true;
}

}  // namespace

bool initAudioStorage() {
  if (audioStorageReady) {
    return true;
  }

  audioStorageReady = LittleFS.begin(
    false,
    "/littlefs",
    10,
    kAudioPartition
  );

  if (!audioStorageReady) {
    serialLogPrintln("LittleFS mount failed (run: pio run -t uploadfs)");
    return false;
  }

  for (uint8_t i = 0; i < kWavClipCount; i++) {
    const WavClip clip = static_cast<WavClip>(i);
    const char* path = wavClipPath(clip);

    if (!LittleFS.exists(path)) {
      serialLogPrint(path);
      serialLogPrintln(" not on LittleFS");
      logAudioStorageContents();
    }
  }

  loadAudioCues();
  return true;
}

bool initI2sBus() {
  // Full duplex: speaker data out on I2S_DIN, mic data in on I2S_MIC_SD, one
  // BCLK/WS pair. 32-bit slots give the INMP441 its 64 BCLK per frame.
  I2S.setPins(
    I2S_BCLK,
    I2S_LRC,
    I2S_DIN,
    I2S_MIC_SD
  );

  if (!I2S.begin(
        I2S_MODE_STD,
        SAMPLE_RATE,
        I2S_DATA_BIT_WIDTH_32BIT,
        I2S_SLOT_MODE_STEREO
      )) {
    // Keep the speaker (and the robot) up when the RX half is refused; the mic
    // task sees no RX channel and stays off.
    serialLogPrintln("I2S full duplex failed; retrying speaker only (mic off)");
    I2S.end();
    I2S.setPins(I2S_BCLK, I2S_LRC, I2S_DIN);
    return I2S.begin(
      I2S_MODE_STD,
      SAMPLE_RATE,
      I2S_DATA_BIT_WIDTH_32BIT,
      I2S_SLOT_MODE_STEREO
    );
  }

  // INMP441 SD is tri-state outside its slot (and floats when no mic is fitted):
  // pull it down so a missing mic reads a constant 0.
  gpio_pullup_dis(static_cast<gpio_num_t>(I2S_MIC_SD));
  gpio_pulldown_en(static_cast<gpio_num_t>(I2S_MIC_SD));
  return true;
}

void writeMonoToSpeaker(const int16_t* samples, size_t count) {
  const uint8_t volume = settingsVolume();

  while (count > 0) {
    const size_t framesThisTime = count < kSpeakerFrames ? count : kSpeakerFrames;

    i2sPackMonoToStereo32(samples, framesThisTime, volume, g_speakerFrames);
    writeSpeakerFrames(framesThisTime);

    samples += framesThisTime;
    count -= framesThisTime;
  }
}

void playTone(
  float frequency,
  int durationMs
) {
  float phase = 0.0f;

  const float phaseStep =
    2.0f * PI * frequency / SAMPLE_RATE;

  const float amplitude =
    32767.0f * settingsVolume() / 100.0f;

  const int totalFrames =
    SAMPLE_RATE * durationMs / 1000;

  int framesPlayed = 0;

  while (framesPlayed < totalFrames) {
    const int framesThisTime =
      min(
        static_cast<int>(kSpeakerFrames),
        totalFrames - framesPlayed
      );

    for (int i = 0; i < framesThisTime; i++) {
      const int32_t slot = i2sSlotFromSample(
        (int16_t)(
          sin(phase) * amplitude
        )
      );

      phase += phaseStep;

      if (phase >= 2.0f * PI) {
        phase -= 2.0f * PI;
      }

      g_speakerFrames[i * 2] = slot;
      g_speakerFrames[i * 2 + 1] = slot;
    }

    writeSpeakerFrames(framesThisTime);

    framesPlayed += framesThisTime;
  }
}

void playSilence(int durationMs) {
  const int totalFrames =
    SAMPLE_RATE * durationMs / 1000;

  int framesPlayed = 0;

  memset(g_speakerFrames, 0, sizeof(g_speakerFrames));

  while (framesPlayed < totalFrames) {
    const int framesThisTime =
      min(
        static_cast<int>(kSpeakerFrames),
        totalFrames - framesPlayed
      );

    writeSpeakerFrames(framesThisTime);

    framesPlayed += framesThisTime;
  }
}

void stopAllWavPlayback() {
  wavStreamStop();
}

void stopBellPlayback() {
  wavStreamStop();
}

void stopWelcomePlayback() {
  wavStreamStop();
}

void stopAttentionPlayback() {
  wavStreamStop();
}

void stopErrorPlayback() {
  wavStreamStop();
}

void stopAbortPlayback() {
  wavStreamStop();
}

void stopDeadPlayback() {
  wavStreamStop();
}

bool startBellPlayback() {
  return wavStreamStart(WavClip::Bell);
}

bool startWelcomePlayback() {
  return wavStreamStart(WavClip::Welcome);
}

bool startAttentionPlayback() {
  return wavStreamStart(WavClip::Attention);
}

bool startErrorPlayback() {
  return wavStreamStart(WavClip::Error);
}

bool startAbortPlayback() {
  return wavStreamStart(WavClip::Abort);
}

bool startDeadPlayback() {
  return wavStreamStart(WavClip::Dead);
}

bool updateBellPlayback() {
  return wavStreamUpdate();
}

bool updateWelcomePlayback() {
  return wavStreamUpdate();
}

bool updateAttentionPlayback() {
  return wavStreamUpdate();
}

bool updateErrorPlayback() {
  return wavStreamUpdate();
}

bool updateAbortPlayback() {
  return wavStreamUpdate();
}

bool updateDeadPlayback() {
  return wavStreamUpdate();
}

bool playBell() {
  return playWavBlocking(WavClip::Bell, "Playing...");
}

bool playWelcome() {
  return playWavBlocking(WavClip::Welcome, "Playing...");
}

bool playAttention() {
  return playWavBlocking(WavClip::Attention, "Playing...");
}

bool playError() {
  return playWavBlocking(WavClip::Error, "Playing...");
}

bool playAbort() {
  return playWavBlocking(WavClip::Abort, "Playing...");
}

bool playDead() {
  return playWavBlocking(WavClip::Dead, "Playing...");
}

void runSoundTest() {
  serialLogPrintln();
  serialLogPrintln("==========================");
  serialLogPrintln("SOUND TEST");
  serialLogPrintln("==========================");

  showOledText(
    "SOUND TEST",
    "500 Hz"
  );

  playTone(500, 120);
  playSilence(50);

  showOledText(
    "SOUND TEST",
    "700 Hz"
  );

  playTone(700, 120);
  playSilence(50);

  showOledText(
    "SOUND TEST",
    "1000 Hz"
  );

  playTone(1000, 220);
  playSilence(100);

  showOledText(
    "SOUND TEST",
    "OK"
  );

  serialLogPrintln("Sound OK");

  delay(500);
}
