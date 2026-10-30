// RGB-світлодіод (активний LOW) + динамік/бузер на GPIO26
#include "app.h"

#define CH_R 1
#define CH_G 2
#define CH_B 3
#define CH_SPK 4   // таймер 2 (група 0) — лише бузер; підсвітка на каналі 8 (група 1), див. hw.cpp

// Градація (без модальних вікон і кнопок — лише колір екрана, LED і звук), кожен рівень — свій малюнок звуку:
// ЖОВТИЙ — бурштиновий «дихаючий» LED + один м'який сигнал 900 Гц (якщо увімкнено «звук на жовтому»).
// ПОМАРАНЧЕВИЙ — LED пульсує частіше + 3 коротких «біп» 1250 Гц, нагадування кожні 90 с.
// ЧЕРВОНИЙ — миготливий червоний LED + сирена (плавний свіп 700–1200 Гц) 8 с, нагадування щохвилини.
// БАЛІСТИКА (Іскандер/KN-23/Кинджал) — інший малюнок: швидке «стакато» 2000/1400 Гц: на червоному 6 с і
// нагадування кожні 30 с, на помаранчевому 3 с і щохвилини.
// ВІДБІЙ — два висхідні тони. ВТРАТА ЗВ'ЯЗКУ — два низькі короткі 440 Гц (один раз при переході).
// Нічний тихий режим (23:00–07:00, налаштування): звучить лише червоний. Дотик до картки — тиша 10 хв;
// підвищення до червоного під час тиші все одно звучить (нова небезпека важливіша за тишу).
enum Pat : uint8_t { P_NONE, P_YELLOW, P_ORANGE, P_RED, P_BALLISTIC, P_CLEAR, P_STALE };
static Level s_level = LVL_GREEN;
static bool s_near = false, s_ball = false;
static Pat s_pat = P_NONE;
static uint32_t s_soundUntil = 0, s_nextBeep = UINT32_MAX, s_t0 = 0, s_muteUntil = 0;

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
  static uint32_t cur = 0;
  if (f == cur) return;
  cur = f;
  if (!f) { ledcWrite(CH_SPK, 0); return; }
  ledcWriteTone(CH_SPK, f);
  ledcWrite(CH_SPK, 96);  // гучність (скважність)
}

bool notifyNight() {
  if (!g_set.nightQuiet) return false;
  struct tm tm;
  if (!getLocalTime(&tm, 0)) return false;   // час ще не синхронізовано — не вгадуємо
  return tm.tm_hour >= 23 || tm.tm_hour < 7;
}
uint32_t notifyMutedLeftMs() { int32_t d = (int32_t)(s_muteUntil - millis()); return s_muteUntil && d > 0 ? d : 0; }
static bool allowed(Pat p) {   // чи можна звучати зараз (звук увімкнено, не тиша, не ніч — крім червоного)
  if (!g_set.sound) return false;
  bool red = p == P_RED || p == P_BALLISTIC;
  if (notifyMutedLeftMs()) return false;
  if (notifyNight() && !red) return false;
  return true;
}
static void play(Pat p, uint32_t durMs) {
  if (!allowed(p)) { s_pat = P_NONE; return; }
  s_pat = p; s_t0 = millis(); s_soundUntil = s_t0 + durMs;
}

void notifyLevel(Level l, bool near, bool ballistic) {
  bool escalRed = l == LVL_RED && s_level < LVL_RED;
  if (escalRed) s_muteUntil = 0;   // нова небезпека поруч — тиша скасовується
  s_level = l; s_near = near; s_ball = ballistic && l >= LVL_ORANGE;
  uint32_t now = millis();
  s_t0 = now;
  switch (l) {
    case LVL_RED:
      if (s_ball) { play(P_BALLISTIC, 6000); s_nextBeep = now + 30000; }
      else { play(P_RED, 8000); s_nextBeep = now + 60000; }
      break;
    case LVL_ORANGE:
      if (s_ball) { play(P_BALLISTIC, 3000); s_nextBeep = now + 60000; }   // балістика в області
      else { play(P_ORANGE, 1500); s_nextBeep = now + 90000; }
      break;
    case LVL_YELLOW:
      if (g_set.alertOnYellow) play(P_YELLOW, 250); else s_pat = P_NONE;
      s_nextBeep = UINT32_MAX; break;
    default: play(P_CLEAR, 600); s_nextBeep = UINT32_MAX; break;
  }
}

void notifySilence() { s_pat = P_NONE; s_nextBeep = UINT32_MAX; tone_(0); }
void notifyMute(uint32_t ms) { s_muteUntil = millis() + ms; if (!s_muteUntil) s_muteUntil = 1; s_pat = P_NONE; tone_(0); }
void notifyTest(Level l) { uint32_t m = s_muteUntil; s_muteUntil = 0; notifyLevel(l, true, false); s_muteUntil = m; }
void notifyStaleBeep() { if (s_pat == P_NONE || millis() >= s_soundUntil) play(P_STALE, 420); }

void notifyLoop() {
  uint32_t now = millis(), t = now - s_t0;
  // --- LED ---
  switch (s_level) {
    case LVL_RED: { bool on = (t / (s_ball ? 120 : 250)) % 2; ledRGB(on ? 255 : 20, 0, s_ball && on ? 60 : 0); break; }
    case LVL_ORANGE: { uint8_t v = 60 + (uint8_t)(195 * (1 + sinf(t / 150.0f)) / 2); ledRGB(v, v * 90 / 255, 0); break; }
    case LVL_YELLOW: { uint8_t v = 40 + (uint8_t)(100 * (1 + sinf(t / 400.0f)) / 2); ledRGB(v, v * 3 / 4, 0); break; }
    default: ledRGB(0, t < 3000 ? 40 : 3, 0); break;
  }
  if (s_muteUntil && !notifyMutedLeftMs()) s_muteUntil = 0;
  // --- звук ---
  if (s_pat != P_NONE && now < s_soundUntil) {
    switch (s_pat) {
      case P_RED: tone_(700 + (uint32_t)(500 * (1 + sinf(t / 180.0f)) / 2)); break;            // сирена
      case P_BALLISTIC: tone_((t / 110) % 3 == 2 ? 0 : ((t / 110) % 3 ? 1400 : 2000)); break;  // стакато
      case P_ORANGE: tone_((t / 250) % 2 == 0 ? 1250 : 0); break;                              // 3 коротких біп
      case P_YELLOW: tone_(900); break;                                                        // м'який сигнал
      case P_STALE: tone_(t < 140 || (t >= 260 && t < 400) ? 440 : 0); break;                  // 2 низькі
      default: tone_(t < 300 ? 880 : 1320); break;                                             // відбій
    }
    return;
  }
  tone_(0);
  if (s_pat != P_NONE) s_pat = P_NONE;
  if (now > s_nextBeep) {   // нагадування: червоний — щохвилини (балістика — кожні 30 с), помаранчевий — 1.5 хв
    if (s_level == LVL_RED) { play(s_ball ? P_BALLISTIC : P_RED, s_ball ? 2000 : 1500); s_nextBeep = now + (s_ball ? 30000 : 60000); }
    else if (s_level == LVL_ORANGE) { play(s_ball ? P_BALLISTIC : P_ORANGE, 1500); s_nextBeep = now + (s_ball ? 60000 : 90000); }
    else s_nextBeep = UINT32_MAX;
  }
}
