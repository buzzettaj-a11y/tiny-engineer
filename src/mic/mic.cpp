#include "mic/mic.h"

#include <Arduino.h>
#include <ESP_I2S.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#include <atomic>
#include <cstring>

#include "animation.h"
#include "audio/audio.h"
#include "display/eyes/core/internal.h"
#include "hardware/activity.h"
#include "hardware/rgb.h"
#include "mic/mic_protocol.h"
#include "pins.h"
#include "serial_log.h"
#include "settings/settings.h"

namespace {

// Same NVS namespace as settings; internal key like the servo pose `spose`.
constexpr const char* kMicNs = "te";
constexpr const char* kMicMuteKey = "mic_mute";

// ESP-IDF FreeRTOS stack sizes are in bytes. getaddrinfo() and lwIP sendto()
// run on this stack; all audio buffers are static.
constexpr uint32_t kTaskStackBytes = 6144;
// Above loop() (1), below lwIP (18) and Wi-Fi (23). It sleeps in the I2S read.
constexpr UBaseType_t kTaskPriority = 2;
constexpr uint32_t kReadTimeoutMs = 100;
constexpr uint32_t kResolveRetryMs = 5000;
constexpr uint32_t kResolveRefreshMs = 60000;

constexpr size_t kFrames = kMicSamplesPerPacket;

struct MicConfig {
  char host[SETTINGS_BRAIN_HOST_MAX_LEN + 1];
  uint16_t port;
  uint8_t shift;
  uint32_t generation;
};

// Task-owned buffers (static: off the task stack).
int32_t g_rx[kFrames * 2];
int16_t g_samples[kFrames];
uint8_t g_packet[kMicPacketMaxLen];
WiFiUDP g_udp;

TaskHandle_t g_task = nullptr;
SemaphoreHandle_t g_configLock = nullptr;
MicConfig g_config = {};  // guarded by g_configLock

// Cross-task state: plain atomic loads/stores only. The ESP32-C3 (RV32IMC) has
// no atomic read-modify-write instructions, so every counter has one writer.
std::atomic<bool> g_muted{false};
std::atomic<uint32_t> g_buttonPresses{0};  // written by the button reader only
std::atomic<bool> g_present{false};
std::atomic<bool> g_streaming{false};
std::atomic<uint16_t> g_levelPermille{0};
std::atomic<uint32_t> g_sendFailures{0};     // mic task only
std::atomic<uint32_t> g_resolveFailures{0};  // mic task only

// Loop-task state.
bool g_indicatorMuted = false;
bool g_loggedPresent = false;
bool g_loggedStreaming = false;
uint32_t g_loggedSendFailures = 0;
uint32_t g_loggedResolveFailures = 0;
uint32_t g_handledButtonPresses = 0;
MicButton g_loopButton;

void saveMuted(bool muted) {
  Preferences prefs;

  if (!prefs.begin(kMicNs, false)) {
    serialLogPrintln("[mic] mute NVS open failed");
    return;
  }

  prefs.putBool(kMicMuteKey, muted);
  prefs.end();
}

bool loadMuted() {
  Preferences prefs;

  if (!prefs.begin(kMicNs, true)) {
    return false;
  }

  // isKey first: Preferences logs ERROR for a missing key.
  const bool muted = prefs.isKey(kMicMuteKey) && prefs.getBool(kMicMuteKey, false);
  prefs.end();
  return muted;
}

bool buttonRawPressed() {
  return digitalRead(MIC_MUTE_BUTTON_PIN) == LOW;
}

// Called by whichever task reads the button (never both).
void onButtonPress() {
  g_muted.store(!g_muted.load(std::memory_order_relaxed), std::memory_order_relaxed);
  g_buttonPresses.store(g_buttonPresses.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
}

void countFailure(std::atomic<uint32_t>& counter) {
  counter.store(counter.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
}

// Pushes brain_host / brain_port / mic_shift to the task when they change.
void syncConfig() {
  if (g_configLock == nullptr) {
    return;
  }

  const char* host = settingsBrainHost();
  const uint16_t port = settingsBrainPort();
  const uint8_t shift = settingsMicShift();

  if (xSemaphoreTake(g_configLock, 0) != pdTRUE) {
    return;
  }

  if (strcmp(g_config.host, host) != 0 || g_config.port != port || g_config.shift != shift) {
    strlcpy(g_config.host, host, sizeof(g_config.host));
    g_config.port = port;
    g_config.shift = shift;
    g_config.generation++;
  }

  xSemaphoreGive(g_configLock);
}

class Destination {
public:
  void configure(const MicConfig& config) {
    config_ = config;
    resolved_ = false;
    tried_ = false;
    literal_ = ip_.fromString(config_.host);
    resolved_ = literal_;
  }

  bool enabled() const {
    return config_.host[0] != '\0';
  }

  // Resolves hostnames on this task (may block for a DNS round trip).
  bool ready(uint32_t nowMs) {
    if (!enabled()) {
      return false;
    }

    if (literal_) {
      return true;
    }

    const uint32_t since = nowMs - lastResolveMs_;
    const bool due = !tried_ ||
                     (!resolved_ && since >= kResolveRetryMs) ||
                     (resolved_ && since >= kResolveRefreshMs);

    if (due) {
      tried_ = true;
      lastResolveMs_ = nowMs;
      IPAddress fresh;

      if (WiFi.hostByName(config_.host, fresh) == 1) {
        ip_ = fresh;
        resolved_ = true;
      } else {
        countFailure(g_resolveFailures);
      }
    }

    return resolved_;
  }

  bool send(const uint8_t* data, size_t length) {
    if (g_udp.beginPacket(ip_, config_.port) != 1) {
      return false;
    }

    g_udp.write(data, length);
    return g_udp.endPacket() == 1;
  }

  uint8_t shift() const {
    return config_.shift == 0 ? SETTINGS_DEFAULT_MIC_SHIFT : config_.shift;
  }

private:
  MicConfig config_ = {};
  IPAddress ip_;
  bool literal_ = false;
  bool resolved_ = false;
  bool tried_ = false;
  uint32_t lastResolveMs_ = 0;
};

void micTask(void* /*arg*/) {
  const i2s_chan_handle_t rx = I2S.rxChan();
  Destination destination;
  uint32_t generation = UINT32_MAX;
  uint32_t seq = 0;
  bool heartbeatSent = false;
  uint32_t lastHeartbeatMs = 0;
  MicButton button;
  MicPresence presence;
  MicLevel level;

  for (;;) {
    size_t bytes = 0;
    const esp_err_t err = i2s_channel_read(rx, g_rx, sizeof(g_rx), &bytes, kReadTimeoutMs);
    const uint32_t now = millis();

    if (err != ESP_OK && err != ESP_ERR_TIMEOUT) {
      // Channel not running: do not spin.
      vTaskDelay(pdMS_TO_TICKS(50));
    }

    if (button.update(buttonRawPressed(), now)) {
      onButtonPress();
    }

    if (xSemaphoreTake(g_configLock, 0) == pdTRUE) {
      if (g_config.generation != generation) {
        generation = g_config.generation;
        destination.configure(g_config);
      }
      xSemaphoreGive(g_configLock);
    }

    const bool muted = g_muted.load(std::memory_order_relaxed);
    const size_t frames = bytes / (2 * sizeof(int32_t));

    if (frames > 0) {
      presence.feed(g_rx, frames);
      if (presence.present()) {
        g_present.store(true, std::memory_order_relaxed);
      }

      micConvertLeftSlots(g_rx, frames, destination.shift(), g_samples);
      level.update(muted ? 0 : micBlockLevelPermille(g_samples, frames));
      g_levelPermille.store(level.permille(), std::memory_order_relaxed);
    }

    if (!activityWifiUp() || !destination.ready(now)) {
      g_streaming.store(false, std::memory_order_relaxed);
      continue;
    }

    const uint16_t flags = micFlagBits(
      muted,
      activitySpeaker(now),
      activityMoving(now),
      activityAsleep()
    );

    if (muted) {
      g_streaming.store(false, std::memory_order_relaxed);

      if (!heartbeatSent || now - lastHeartbeatMs >= kMicHeartbeatMs) {
        heartbeatSent = true;
        lastHeartbeatMs = now;
        micWriteHeader(g_packet, seq++, now, flags, 0);
        if (!destination.send(g_packet, kMicHeaderLen)) {
          countFailure(g_sendFailures);
        }
      }
      continue;
    }

    heartbeatSent = false;

    if (frames == 0) {
      continue;
    }

    micWriteHeader(
      g_packet,
      seq++,
      micBlockStartMs(now, frames, SAMPLE_RATE),
      flags,
      static_cast<uint16_t>(frames)
    );
    micWriteSamplesLe(g_packet + kMicHeaderLen, g_samples, frames);

    const bool sent = destination.send(g_packet, kMicHeaderLen + frames * sizeof(int16_t));
    g_streaming.store(sent, std::memory_order_relaxed);

    if (!sent) {
      countFailure(g_sendFailures);
    }
  }
}

void refreshIndicator(uint32_t nowMs) {
  const bool muted = g_muted.load(std::memory_order_relaxed);

  if (muted == g_indicatorMuted) {
    return;
  }

  g_indicatorMuted = muted;
  // OLED mark is drawn with the eyes; the RGB LED goes dim amber where the
  // animation would leave it off.
  eyes::requestForceRedraw();
  setRgbForAnimation(getAnimation(), nowMs);
}

void logChanges() {
  if (!g_loggedPresent && g_present.load(std::memory_order_relaxed)) {
    g_loggedPresent = true;
    serialLogPrintln("[mic] INMP441 data seen");
  }

  const bool streaming = g_streaming.load(std::memory_order_relaxed);

  if (streaming != g_loggedStreaming) {
    g_loggedStreaming = streaming;
    serialLogPrint("[mic] streaming ");
    serialLogPrintln(streaming ? "on" : "off");
  }

  const uint32_t sendFailures = g_sendFailures.load(std::memory_order_relaxed);
  const uint32_t resolveFailures = g_resolveFailures.load(std::memory_order_relaxed);

  if (resolveFailures != g_loggedResolveFailures) {
    g_loggedResolveFailures = resolveFailures;
    serialLogPrint("[mic] cannot resolve brain_host ");
    serialLogPrintln(settingsBrainHost());
  }

  // One line per 100 failed packets, not 86 a second.
  if (sendFailures / 100 != g_loggedSendFailures / 100) {
    serialLogPrint("[mic] UDP send failures: ");
    serialLogPrintln(sendFailures);
  }
  g_loggedSendFailures = sendFailures;
}

}  // namespace

void initMic() {
  pinMode(MIC_MUTE_BUTTON_PIN, INPUT_PULLUP);

  const bool muted = loadMuted();
  g_muted.store(muted, std::memory_order_relaxed);
  // Set apart from g_muted so the first micTick() paints a muted indicator.
  g_indicatorMuted = false;

  g_configLock = xSemaphoreCreateMutex();
  syncConfig();

  serialLogPrint("[mic] muted=");
  serialLogPrintln(muted ? "yes" : "no");

  if (g_configLock == nullptr || I2S.rxChan() == nullptr) {
    serialLogPrintln("[mic] no I2S RX channel; mic stream off");
    return;
  }

  if (xTaskCreate(micTask, "mic", kTaskStackBytes, nullptr, kTaskPriority, &g_task) != pdPASS) {
    g_task = nullptr;
    serialLogPrintln("[mic] task start failed; mic stream off");
    return;
  }

  serialLogPrintln("[mic] task started");
}

void micTick(uint32_t nowMs) {
  syncConfig();

  // Without the task the button is still read here (loop rate).
  if (g_task == nullptr && g_loopButton.update(buttonRawPressed(), nowMs)) {
    onButtonPress();
  }

  const uint32_t presses = g_buttonPresses.load(std::memory_order_relaxed);

  if (presses != g_handledButtonPresses) {
    g_handledButtonPresses = presses;
    const bool muted = g_muted.load(std::memory_order_relaxed);
    saveMuted(muted);
    serialLogPrint("[mic] button: muted=");
    serialLogPrintln(muted ? "yes" : "no");
  }

  refreshIndicator(nowMs);
  logChanges();
}

bool micMuted() {
  return g_muted.load(std::memory_order_relaxed);
}

void micSetMuted(bool muted) {
  g_muted.store(muted, std::memory_order_relaxed);
  saveMuted(muted);
  serialLogPrint("[mic] muted=");
  serialLogPrintln(muted ? "yes" : "no");
  refreshIndicator(millis());
}

MicStatus micStatus() {
  MicStatus status{};
  status.muted = g_muted.load(std::memory_order_relaxed);
  status.streaming = !status.muted && g_streaming.load(std::memory_order_relaxed);
  status.present = g_present.load(std::memory_order_relaxed);
  status.level = status.muted ? 0.0f : g_levelPermille.load(std::memory_order_relaxed) / 1000.0f;
  return status;
}
