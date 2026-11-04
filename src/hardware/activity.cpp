#include "hardware/activity.h"

#include <atomic>

#include "mic/mic_protocol.h"

namespace {

// Audio still queued in the I2S TX DMA when a write returns: ESP_I2S uses
// 6 descriptors x 240 frames = 1440 frames ~ 65 ms at 22050 Hz.
constexpr uint32_t kSpeakerDmaTailMs = 70;
// A joint stepping slower than one PWM count per tick still counts as moving.
constexpr uint32_t kServoWriteTailMs = 150;

std::atomic<bool> g_speakerEver{false};
std::atomic<uint32_t> g_speakerLastMs{0};
std::atomic<bool> g_servoEver{false};
std::atomic<uint32_t> g_servoLastMs{0};
std::atomic<bool> g_moving{false};
std::atomic<bool> g_asleep{false};
std::atomic<bool> g_wifiUp{false};
std::atomic<bool> g_exploring{false};

}  // namespace

void activityNoteSpeakerWrite(uint32_t nowMs) {
  g_speakerLastMs.store(nowMs, std::memory_order_relaxed);
  g_speakerEver.store(true, std::memory_order_relaxed);
}

void activityNoteServoWrite(uint32_t nowMs) {
  g_servoLastMs.store(nowMs, std::memory_order_relaxed);
  g_servoEver.store(true, std::memory_order_relaxed);
}

void activityPublish(bool anyServoMoving, bool asleep, bool wifiUp) {
  g_moving.store(anyServoMoving, std::memory_order_relaxed);
  g_asleep.store(asleep, std::memory_order_relaxed);
  g_wifiUp.store(wifiUp, std::memory_order_relaxed);
}

void activitySetExploring(bool exploring) {
  g_exploring.store(exploring, std::memory_order_relaxed);
}

bool activitySpeaker(uint32_t nowMs) {
  return micSpeakerRecent(
    g_speakerEver.load(std::memory_order_relaxed),
    g_speakerLastMs.load(std::memory_order_relaxed),
    nowMs,
    kMicSpeakerTailMs + kSpeakerDmaTailMs
  );
}

bool activityMoving(uint32_t nowMs) {
  if (g_moving.load(std::memory_order_relaxed) || g_exploring.load(std::memory_order_relaxed)) {
    return true;
  }

  return g_servoEver.load(std::memory_order_relaxed) &&
         static_cast<uint32_t>(nowMs - g_servoLastMs.load(std::memory_order_relaxed)) <
           kServoWriteTailMs;
}

bool activityAsleep() {
  return g_asleep.load(std::memory_order_relaxed);
}

bool activityWifiUp() {
  return g_wifiUp.load(std::memory_order_relaxed);
}
