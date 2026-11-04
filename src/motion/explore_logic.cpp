#include "motion/explore_logic.h"

#include <cmath>

ExploreBound exploreSweepTarget(float savedLimit, float hardLimit, BumpDir dir, float maxExcursionDeg) {
  if (dir == BumpDir::Max) {
    const float cap = savedLimit + maxExcursionDeg;
    return cap < hardLimit ? ExploreBound{cap, true} : ExploreBound{hardLimit, false};
  }

  const float cap = savedLimit - maxExcursionDeg;
  return cap > hardLimit ? ExploreBound{cap, true} : ExploreBound{hardLimit, false};
}

uint16_t exploreThresholdMa(uint16_t bumpThresholdSetting) {
  if (bumpThresholdSetting == 0 || bumpThresholdSetting > EXPLORE_THRESHOLD_CAP_MA) {
    return EXPLORE_THRESHOLD_CAP_MA;
  }
  return bumpThresholdSetting;
}

bool exploreBaselinePlausible(float baselineMa) {
  return std::isfinite(baselineMa) && baselineMa >= EXPLORE_MIN_BASELINE_MA &&
         baselineMa <= EXPLORE_MAX_BASELINE_MA;
}
