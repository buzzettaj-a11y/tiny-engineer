#pragma once

#include "display/eyes.h"

void drawEyes(
  const Eye& left,
  const Eye& right,
  int cornerRadius
);

void drawDeadXEyes(const Eye& left, const Eye& right);

// Every eyes frame ends here instead of display.display(): adds the overlays
// (mic-muted mark) and pushes the buffer to the panel.
void presentEyesFrame();
