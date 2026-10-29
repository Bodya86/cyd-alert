// Stubs for the hardware / network / OTA modules that ui.cpp calls (simulator only)
#include "app.h"
#include "ota.h"
#include <WiFi.h>
#include <stdarg.h>

SimSerial Serial;
SimEsp ESP;
SimWiFi WiFi;
OtaStatus g_ota;

extern "C" {
uint32_t g_sim_ms = 0;
time_t g_sim_epoch = 0;
uint32_t millis(void) { return g_sim_ms; }
void delay(uint32_t) {}
time_t sim_time(time_t* t) { time_t v = g_sim_epoch + g_sim_ms / 1000; if (t) *t = v; return v; }
size_t sim_strlcpy(char* d, const char* s, size_t n) {
  size_t l = strlen(s);
  if (n) { size_t c = l < n - 1 ? l : n - 1; memcpy(d, s, c); d[c] = 0; }
  return l;
}
}
bool getLocalTime(struct tm* info, uint32_t) { time_t t = sim_time(nullptr); localtime_r(&t, info); return true; }

// hw.cpp
int g_sim_bat_mv = -1;
void hwSetBrightness(uint8_t) {}
int hwBatteryMv() { return g_set.battery ? g_sim_bat_mv : -1; }
int hwBatteryPct(int mv) {   // та сама крива LiPo, що й у src/hw.cpp
  static const int16_t V[] = {3300, 3450, 3550, 3650, 3700, 3750, 3800, 3850, 3920, 4000, 4100, 4180};
  static const uint8_t P[] = {   0,    5,   10,   20,   30,   40,   50,   60,   70,   80,   90,  100};
  if (mv <= V[0]) return 0;
  for (int i = 1; i < 12; i++)
    if (mv < V[i]) return P[i - 1] + (P[i] - P[i - 1]) * (mv - V[i - 1]) / (V[i] - V[i - 1]);
  return 100;
}
// notify.cpp
void notifyInit() {}
void notifyLoop() {}
void notifyLevel(Level, bool) {}
void notifySilence() {}
void notifyTest(Level) {}
// netsvc.cpp
void netStart() {}
void netForceRefresh() {}
int netChannelStatus(int, Level* l) { if (l) *l = LVL_GREEN; return 1; }
const char* netCloudSrc() { return "GitHub API"; }
void netOutageRefresh() {}
// ota.cpp
int otaCompare(const char* a, const char* b) {
  int x[3] = {0}, y[3] = {0};
  sscanf(a, "%d.%d.%d", &x[0], &x[1], &x[2]); sscanf(b, "%d.%d.%d", &y[0], &y[1], &y[2]);
  for (int i = 0; i < 3; i++) if (x[i] != y[i]) return x[i] - y[i];
  return 0;
}
void otaTick() {}
void otaRequestCheck() {}
void otaMarkValid() {}
