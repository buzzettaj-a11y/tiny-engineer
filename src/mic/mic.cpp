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

// Three tasks, so that nothing can delay a mute press or the I2S reads:
//   mic     — I2S RX, level/presence, UDP send. Never blocks on anything else.
//   micbtn  — polls GP7 every 5 ms and counts presses. Nothing else.
//   micdns  — resolves brain_host (blocking DNS) into an IPv4 + port.
// ESP-IDF FreeRTOS stack sizes are in bytes; all audio buffers are static.
constexpr uint32_t kMicStackBytes = 4096;
constexpr uint32_t kButtonStackBytes = 2048;
constexpr uint32_t kDnsStackBytes = 4096;
// Above loop() (1), below lwIP (18) and Wi-Fi (23). They all sleep most of the time.
constexpr UBaseType_t kMicPriority = 2;
constexpr UBaseType_t kButtonPriority = 3;
constexpr UBaseType_t kDnsPriority = 1;
constexpr uint32_t kReadTimeoutMs = 100;
constexpr uint32_t kButtonPollMs = 5;
constexpr uint32_t kDnsPollMs = 100;
constexpr uint32_t kResolveRetryMs = 5000;
constexpr uint32_t kResolveRefreshMs = 60000;

constexpr size_t kFrames = kMicSamplesPerPacket;

// Task-owned buffers (static: off the task stacks).
int32_t g_rx[kFrames * 2];
int16_t g_samples[kFrames];
uint8_t g_packet[kMicPacketMaxLen];
WiFiUDP g_udp;

TaskHandle_t g_micTask = nullptr;
TaskHandle_t g_buttonTask = nullptr;

// brain_host / brain_port for the DNS task; written by the loop task under the lock.
SemaphoreHandle_t g_hostLock = nullptr;
char g_host[SETTINGS_BRAIN_HOST_MAX_LEN + 1] = {};
uint16_t g_hostPort = 0;
uint32_t g_hostGeneration = 0;

// Cross-task state: plain atomic loads/stores only. The ESP32-C3 (RV32IMC) has
// no atomic read-modify-write instructions, so every value has one writer.
std::atomic<uint32_t> g_buttonPresses{0};  // button reader only
std::atomic<uint32_t> g_muteState{0};      // loop task only (micPackMuteState)
std::atomic<uint8_t> g_shift{SETTINGS_DEFAULT_MIC_SHIFT};  // loop task only
std::atomic<uint16_t> g_destPort{0};       // DNS task only
std::atomic<uint32_t> g_destIp{0};         // DNS task only; 0 = nowhere to send
std::atomic<bool> g_present{false};        // mic task only
std::atomic<bool> g_streaming{false};      // mic task only
std::atomic<uint16_t> g_levelPermille{0};  // mic task only
std::atomic<uint32_t> g_sendFailures{0};   // mic task only
std::atomic<uint32_t> g_resolveFailures{0};  // DNS task only

// Loop-task state.
bool g_indicatorMuted = false;
bool g_loggedPresent = false;
bool g_loggedStreaming = false;
uint32_t g_loggedSendFailures = 0;
uint32_t g_loggedResolveFailures = 0;
char g_syncedHost[SETTINGS_BRAIN_HOST_MAX_LEN + 1] = {};
uint16_t g_syncedPort = 0;
bool g_hostSynced = false;
MicButton g_loopButton;

void bump(std::atomic<uint32_t>& counter) {
  counter.store(counter.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
}

bool effectiveMuted() {
  return micEffectiveMuted(
    g_muteState.load(std::memory_order_acquire),
    g_buttonPresses.load(std::memory_order_acquire)
  );
}

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

// Loop task: pushes settings to the tasks when they change.
void syncConfig() {
  g_shift.store(settingsMicShift(), std::memory_order_relaxed);

  const char* host = settingsBrainHost();
  const uint16_t port = settingsBrainPort();

  if (g_hostSynced && strcmp(g_syncedHost, host) == 0 && g_syncedPort == port) {
    return;
  }

  if (g_hostLock == nullptr || xSemaphoreTake(g_hostLock, 0) != pdTRUE) {
    return;
  }

  strlcpy(g_host, host, sizeof(g_host));
  g_hostPort = port;
  g_hostGeneration++;
  xSemaphoreGive(g_hostLock);

  strlcpy(g_syncedHost, host, sizeof(g_syncedHost));
  g_syncedPort = port;
  g_hostSynced = true;
}

void publishDestination(uint32_t ip, uint16_t port) {
  // ip goes to 0 first so the mic task never pairs a new port with an old ip
  // for long (at most one packet).
  g_destIp.store(0, std::memory_order_release);
  g_destPort.store(port, std::memory_order_release);
  g_destIp.store(ip, std::memory_order_release);
}

void buttonTask(void* /*arg*/) {
  MicButton button;

  for (;;) {
    if (button.update(buttonRawPressed(), millis())) {
      // Counted only; the loop task applies it. The mic task honours a pending
      // press at once (micEffectiveMuted), so no audio leaves after a press.
      g_buttonPresses.store(
        g_buttonPresses.load(std::memory_order_relaxed) + 1,
        std::memory_order_release
      );
    }

    vTaskDelay(pdMS_TO_TICKS(kButtonPollMs));
  }
}

void dnsTask(void* /*arg*/) {
  char host[SETTINGS_BRAIN_HOST_MAX_LEN + 1] = {};
  uint32_t generation = UINT32_MAX;
  uint16_t port = 0;
  bool literal = false;
  bool resolved = false;
  bool tried = false;
  uint32_t lastResolveMs = 0;

  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(kDnsPollMs));

    if (xSemaphoreTake(g_hostLock, pdMS_TO_TICKS(10)) == pdTRUE) {
      if (g_hostGeneration != generation) {
        generation = g_hostGeneration;
        strlcpy(host, g_host, sizeof(host));
        port = g_hostPort;
        tried = false;
        resolved = false;
        IPAddress ip;
        literal = host[0] != '\0' && ip.fromString(host);
        publishDestination(literal ? static_cast<uint32_t>(ip) : 0, port);
        resolved = literal;
      }
      xSemaphoreGive(g_hostLock);
    }

    if (host[0] == '\0' || literal || !activityWifiUp()) {
      continue;
    }

    const uint32_t now = millis();
    const uint32_t since = now - lastResolveMs;
    const bool due = !tried ||
                     (!resolved && since >= kResolveRetryMs) ||
                     (resolved && since >= kResolveRefreshMs);

    if (!due) {
      continue;
    }

    tried = true;
    lastResolveMs = now;
    IPAddress fresh;

    // IPv4 only: operator uint32_t() is 0 for an IPv6 answer.
    if (WiFi.hostByName(host, fresh) == 1 && static_cast<uint32_t>(fresh) != 0) {
      resolved = true;
      publishDestination(static_cast<uint32_t>(fresh), port);
    } else {
      bump(g_resolveFailures);
    }
  }
}

bool sendPacket(uint32_t ip, uint16_t port, const uint8_t* data, size_t length) {
  if (g_udp.beginPacket(IPAddress(ip), port) != 1) {
    return false;
  }

  g_udp.write(data, length);
  return g_udp.endPacket() == 1;
}

void micTask(void* /*arg*/) {
  const i2s_chan_handle_t rx = I2S.rxChan();
  uint32_t seq = 0;
  bool heartbeatSent = false;
  uint32_t lastHeartbeatMs = 0;
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

    const size_t frames = bytes / (2 * sizeof(int32_t));

    if (frames > 0) {
      presence.feed(g_rx, frames);
      if (presence.present()) {
        g_present.store(true, std::memory_order_relaxed);
      }

      micConvertLeftSlots(g_rx, frames, g_shift.load(std::memory_order_relaxed), g_samples);
      level.update(effectiveMuted() ? 0 : micBlockLevelPermille(g_samples, frames));
      g_levelPermille.store(level.permille(), std::memory_order_relaxed);
    }

    const uint32_t ip = g_destIp.load(std::memory_order_acquire);
    const uint16_t port = g_destPort.load(std::memory_order_acquire);

    if (!activityWifiUp() || ip == 0 || port == 0) {
      g_streaming.store(false, std::memory_order_relaxed);
      continue;
    }

    // Read last, right before sending: a press counted a moment ago wins.
    const bool muted = effectiveMuted();
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
        if (!sendPacket(ip, port, g_packet, kMicHeaderLen)) {
          bump(g_sendFailures);
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

    const bool sent = sendPacket(ip, port, g_packet, kMicHeaderLen + frames * sizeof(int16_t));
    g_streaming.store(sent, std::memory_order_relaxed);

    if (!sent) {
      bump(g_sendFailures);
    }
  }
}

// Loop task: folds counted presses into the saved state.
void applyButtonPresses() {
  const uint32_t presses = g_buttonPresses.load(std::memory_order_acquire);
  const uint32_t state = g_muteState.load(std::memory_order_relaxed);

  if ((presses & 0x7FFFFFFFu) == micStateApplied(state)) {
    return;
  }

  const bool muted = micEffectiveMuted(state, presses);
  g_muteState.store(micPackMuteState(presses, muted), std::memory_order_release);
  saveMuted(muted);
  serialLogPrint("[mic] button: muted=");
  serialLogPrintln(muted ? "yes" : "no");
}

void refreshIndicator(uint32_t nowMs) {
  const bool muted = effectiveMuted();

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
  g_muteState.store(micPackMuteState(0, muted), std::memory_order_release);
  // Set apart from the state so the first micTick() paints a muted indicator.
  g_indicatorMuted = false;

  g_hostLock = xSemaphoreCreateMutex();
  syncConfig();

  serialLogPrint("[mic] muted=");
  serialLogPrintln(muted ? "yes" : "no");

  // The button works with or without a mic.
  if (xTaskCreate(buttonTask, "micbtn", kButtonStackBytes, nullptr, kButtonPriority, &g_buttonTask) !=
      pdPASS) {
    g_buttonTask = nullptr;
    // The privacy button now depends on loop(): a press during a blocking route
    // (/play, /ranges/explore, tests) can be lost. (No raw Serial print: it
    // would corrupt the framed USB serial protocol.)
    serialLogPrintln("[mic] WARNING: button task start failed; GP7 read in loop() only, "
                     "presses during blocking routes can be missed");
  }

  if (g_hostLock == nullptr || I2S.rxChan() == nullptr) {
    serialLogPrintln("[mic] no I2S RX channel; mic stream off");
    return;
  }

  if (xTaskCreate(dnsTask, "micdns", kDnsStackBytes, nullptr, kDnsPriority, nullptr) != pdPASS) {
    serialLogPrintln("[mic] DNS task start failed; mic stream off");
    return;
  }

  if (xTaskCreate(micTask, "mic", kMicStackBytes, nullptr, kMicPriority, &g_micTask) != pdPASS) {
    g_micTask = nullptr;
    serialLogPrintln("[mic] task start failed; mic stream off");
    return;
  }

  serialLogPrintln("[mic] tasks started");
}

void micTick(uint32_t nowMs) {
  syncConfig();

  // Without the button task the button is read here (loop rate).
  if (g_buttonTask == nullptr && g_loopButton.update(buttonRawPressed(), nowMs)) {
    bump(g_buttonPresses);
  }

  applyButtonPresses();
  refreshIndicator(nowMs);
  logChanges();
}

bool micMuted() {
  return effectiveMuted();
}

void micSetMuted(bool muted) {
  // An explicit set also settles any presses counted so far.
  const uint32_t presses = g_buttonPresses.load(std::memory_order_acquire);
  g_muteState.store(micPackMuteState(presses, muted), std::memory_order_release);
  saveMuted(muted);
  serialLogPrint("[mic] muted=");
  serialLogPrintln(muted ? "yes" : "no");
  refreshIndicator(millis());
}

MicStatus micStatus() {
  MicStatus status{};
  status.muted = effectiveMuted();
  status.streaming = !status.muted && g_streaming.load(std::memory_order_relaxed);
  status.present = g_present.load(std::memory_order_relaxed);
  status.level = status.muted ? 0.0f : g_levelPermille.load(std::memory_order_relaxed) / 1000.0f;
  return status;
}
