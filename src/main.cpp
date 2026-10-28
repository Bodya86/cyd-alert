// CYD Alert — карта повітряних тривог України для ESP32-2432S028 (Cheap Yellow Display)
#include "app.h"
#include <WiFi.h>
#include <lvgl.h>

void setup() {
  Serial.begin(115200);
  Serial.println("\n[CYD Alert] start");
  settingsLoad();
  notifyInit();
  hwInit();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  configTzTime(TZ_INFO, "pool.ntp.org", "time.google.com");
  uiInit();      // сам вирішує: майстер Wi-Fi / вибір локації / головний екран
  netStart();    // фонова задача мережі на core 0
}

void loop() {
  lv_timer_handler();
  uiLoop();
  notifyLoop();
  delay(5);
}
