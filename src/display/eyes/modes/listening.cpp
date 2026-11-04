#include "display/eyes/modes/listening.h"

#include "animation/util.h"
#include "display/eyes.h"
#include "display/eyes/core/constants.h"
#include "display/eyes/core/util.h"

namespace {

// Attentive: taller than idle, lifted a pixel, gaze held on the user with a
// rare one-pixel drift so the face still reads as alive.
constexpr int16_t kListeningHeight = 18;
constexpr int16_t kListeningLift = -1;

int16_t g_driftX = 0;
uint32_t g_nextDriftMs = 0;

}  // namespace

void startListeningEyes(uint32_t now) {
  g_driftX = 0;
  g_nextDriftMs = now + anim::randRangeMs(3000, 6000);
}

void updateListeningEyes(uint32_t now) {
  if (now >= g_nextDriftMs) {
    g_driftX = g_driftX == 0 ? (int16_t)(anim::randChance(50) ? 1 : -1) : 0;
    g_nextDriftMs = now + (g_driftX == 0 ? anim::randRangeMs(3000, 6000) : anim::randRangeMs(600, 1200));
  }

  Eye& left = mutableLeftEye();
  Eye& right = mutableRightEye();

  left = eyes::eyeWithHeight(
    (int16_t)(eyes::DEFAULT_LEFT.x + g_driftX),
    eyes::DEFAULT_LEFT.width,
    kListeningHeight
  );
  right = eyes::eyeWithHeight(
    (int16_t)(eyes::DEFAULT_RIGHT.x + g_driftX),
    eyes::DEFAULT_RIGHT.width,
    kListeningHeight
  );
  left.y += kListeningLift;
  right.y += kListeningLift;
}
