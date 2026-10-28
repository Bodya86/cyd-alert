// RGB-світлодіод (активний LOW) + динамік/бузер на GPIO26
#include "app.h"

#define CH_R 1
#define CH_G 2
#define CH_B 3
#define CH_SPK 4

// ЧЕРВОНИЙ (ракети): миготливий червоний LED + сирена 8 с, нагадування щохвилини.
// ЖОВТИЙ (дрони): «дихаючий» бурштиновий LED + короткі сигнали; якщо ціль поруч — довше і з нагадуванням.
static Level s_level = LVL_GREEN;
static bool s_near = false;
static bool s_soundOn = false;
static uint32_t s_soundUntil = 0, s_nextBeep = 0, s_t0 = 0;

static void ledRGB(uint8_t r, uint8_t g, uint8_t b) {
  ledcWrite(CH_R, 255 - r);
  ledcWrite(CH_G, 255 - g);
  ledcWrite(CH_B, 255 - b);
}

void notifyInit() {
  ledcSetup(CH_R, 5000, 8); ledcAttachPin(PIN_LED_R, CH_R);
  ledcSetup(CH_G, 5000, 8); ledcAttachPin(PIN_LED_G, CH_G);
  ledcSetup(CH_B, 5000, 8); ledcAttachPin(PIN_LED_B, CH_B);
  ledcSetup(CH_SPK, 2000, 8); ledcAttachPin(PIN_SPEAKER, CH_SPK); ledcWrite(CH_SPK, 0);
  ledRGB(0, 0, 0);
}

static void tone_(uint32_t f) {
  if (!f) { ledcWrite(CH_SPK, 0); return; }
  ledcWriteTone(CH_SPK, f);
  ledcWrite(CH_SPK, 96);  // гучність (скважність)
}

void notifyLevel(Level l, bool near) {
  s_level = l;
  s_near = near;
  s_t0 = millis();
  s_soundOn = g_set.sound && l > LVL_GREEN;
  s_soundUntil = s_t0 + (l == LVL_RED ? 8000 : near ? 3000 : 1200);
  s_nextBeep = s_t0 + (l == LVL_RED ? 60000 : 90000);
  if (l == LVL_GREEN && g_set.sound) {  // короткий «відбій»
    s_soundOn = true; s_soundUntil = s_t0 + 600;
  }
}

void notifySilence() { s_soundOn = false; s_nextBeep = UINT32_MAX; tone_(0); }
void notifyTest(Level l) { notifyLevel(l, true); }

void notifyLoop() {
  uint32_t now = millis(), t = now - s_t0;
  // --- LED ---
  switch (s_level) {
    case LVL_RED: { bool on = (t / 250) % 2; ledRGB(on ? 255 : 20, 0, 0); break; }
    case LVL_YELLOW: { uint8_t v = 40 + (uint8_t)(100 * (1 + sinf(t / (s_near ? 160.0f : 400.0f))) / 2); ledRGB(v, v / 2, 0); break; }
    default: ledRGB(0, t < 3000 ? 40 : 3, 0); break;
  }
  // --- звук ---
  if (s_soundOn && now < s_soundUntil) {
    if (s_level == LVL_RED) tone_(700 + (uint32_t)(500 * (1 + sinf(t / 180.0f)) / 2));  // сирена
    else if (s_level == LVL_YELLOW) tone_(((t / 200) % 3) < 2 && (t / 200) % 2 == 0 ? 1100 : 0);
    else tone_(t < 300 ? 880 : 1320);
  } else {
    tone_(0);
    if (s_soundOn && s_level == LVL_RED && now > s_nextBeep) {  // нагадування щохвилини
      s_t0 = now; s_soundUntil = now + 1500; s_nextBeep = now + 60000;
    } else if (s_soundOn && s_level == LVL_YELLOW && s_near && now > s_nextBeep) {  // дрон поруч — раз на 1.5 хв
      s_t0 = now; s_soundUntil = now + 1200; s_nextBeep = now + 90000;
    } else if (now >= s_soundUntil && s_level != LVL_RED && !(s_level == LVL_YELLOW && s_near)) s_soundOn = false;
  }
}
