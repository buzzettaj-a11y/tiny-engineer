#include "display/eyes/core/draw.h"

#include <Arduino.h>
#include <Adafruit_SSD1306.h>

#include "display/oled.h"
#include "display/oled_internal.h"
#include "mic/mic.h"
#include "pins.h"

namespace {

void fillRoundedEye(
  const Eye& eye,
  int cornerRadius
) {
  if (eye.width <= 0 || eye.height <= 0) {
    return;
  }

  const int radius = min(
    cornerRadius,
    min(eye.width / 2, eye.height / 2)
  );

  if (radius <= 0) {
    display.fillRect(
      eye.x,
      eye.y,
      eye.width,
      eye.height,
      SSD1306_WHITE
    );
    return;
  }

  display.fillRect(
    eye.x + radius,
    eye.y,
    eye.width - 2 * radius,
    eye.height,
    SSD1306_WHITE
  );

  display.fillRect(
    eye.x,
    eye.y + radius,
    eye.width,
    eye.height - 2 * radius,
    SSD1306_WHITE
  );

  display.fillCircle(
    eye.x + radius,
    eye.y + radius,
    radius,
    SSD1306_WHITE
  );

  display.fillCircle(
    eye.x + eye.width - radius - 1,
    eye.y + radius,
    radius,
    SSD1306_WHITE
  );

  display.fillCircle(
    eye.x + radius,
    eye.y + eye.height - radius - 1,
    radius,
    SSD1306_WHITE
  );

  display.fillCircle(
    eye.x + eye.width - radius - 1,
    eye.y + eye.height - radius - 1,
    radius,
    SSD1306_WHITE
  );
}

void drawThickLine(
  int16_t x0,
  int16_t y0,
  int16_t x1,
  int16_t y1
) {
  for (int16_t i = -1; i <= 1; i++) {
    display.drawLine(x0 + i, y0, x1 + i, y1, SSD1306_WHITE);
    display.drawLine(x0, y0 + i, x1, y1 + i, SSD1306_WHITE);
  }
}

void drawXInEye(const Eye& eye) {
  if (eye.width <= 2 || eye.height <= 2) {
    return;
  }

  const int16_t insetX = 3;
  const int16_t insetY = 2;
  const int16_t x0 = eye.x + insetX;
  const int16_t y0 = eye.y + insetY;
  const int16_t x1 = eye.x + eye.width - insetX - 1;
  const int16_t y1 = eye.y + eye.height - insetY - 1;
  drawThickLine(x0, y0, x1, y1);
  drawThickLine(x1, y0, x0, y1);
}

// Muted mic: a 7x9 microphone with a slash in the top-right corner, on a
// black patch so it reads over any style (kaomoji faces fill the panel).
void drawMuteMark() {
  constexpr int16_t x = OLED_WIDTH - 9;
  constexpr int16_t y = 0;

  display.fillRect(x - 1, y, 10, 11, SSD1306_BLACK);
  display.drawRoundRect(x + 2, y, 4, 6, 2, SSD1306_WHITE);          // capsule
  display.drawLine(x + 1, y + 4, x + 1, y + 5, SSD1306_WHITE);      // cradle left
  display.drawLine(x + 6, y + 4, x + 6, y + 5, SSD1306_WHITE);      // cradle right
  display.drawLine(x + 2, y + 6, x + 5, y + 6, SSD1306_WHITE);      // cradle bottom
  display.drawLine(x + 3, y + 7, x + 3, y + 8, SSD1306_WHITE);      // stand
  display.drawLine(x, y + 9, x + 7, y, SSD1306_WHITE);              // slash
}

}  // namespace

void presentEyesFrame() {
  if (micMuted()) {
    drawMuteMark();
  }

  display.display();
}

void drawEyes(
  const Eye& left,
  const Eye& right,
  int cornerRadius
) {
  if (!oledAvailable) {
    return;
  }

  display.clearDisplay();
  fillRoundedEye(left, cornerRadius);
  fillRoundedEye(right, cornerRadius);
  presentEyesFrame();
}

void drawDeadXEyes(const Eye& left, const Eye& right) {
  if (!oledAvailable) {
    return;
  }

  display.clearDisplay();
  drawXInEye(left);
  drawXInEye(right);
  presentEyesFrame();
}
