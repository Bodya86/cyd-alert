// OTA: перевірка релізу GitHub (version.json) раз на 6 год і на старті; завантаження HTTPS у вільний слот,
// перевірка розміру та MD5 (Update.h), перезапуск. Ніколи не оновлюється під час помаранчевого/червоного рівня.
#include "app.h"
#include "ota.h"
#include "version.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <Update.h>
#include <esp_ota_ops.h>

OtaStatus g_ota;
static volatile bool s_req = false;
static uint32_t s_next = OTA_FIRST_MS;

static void say(const char* m) { strlcpy(g_ota.msg, m, sizeof(g_ota.msg)); g_ota.msgSeq++; Serial.printf("[ota] %s\n", m); }

int otaCompare(const char* a, const char* b) {
  int x[3] = {0}, y[3] = {0};
  sscanf(a, "%d.%d.%d", &x[0], &x[1], &x[2]);
  sscanf(b, "%d.%d.%d", &y[0], &y[1], &y[2]);
  for (int i = 0; i < 3; i++) if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1;
  return 0;
}

void otaRequestCheck() { g_ota.manual = true; s_req = true; }

void otaMarkValid() {
  esp_ota_mark_app_valid_cancel_rollback();   // якщо bootloader з rollback — нова прошивка стартувала успішно
}

static bool begin(HTTPClient& http, WiFiClientSecure& sec, const String& url) {
  sec.setInsecure();
  sec.setTimeout(20);
  if (!http.begin(sec, url)) return false;
  http.useHTTP10(true);
  http.setTimeout(20000);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.setUserAgent("CYD-Alert/" FW_VERSION " (ESP32)");
  return true;
}

static bool alertActive() {
  stateLock();
  Level l = g_state.level;
  stateUnlock();
  return l >= LVL_ORANGE;
}

static void install(const String& url, int size, const String& md5) {
  WiFiClientSecure sec;
  HTTPClient http;
  g_ota.pct = 0;
  g_ota.state = OTA_DOWNLOADING;
  if (!begin(http, sec, url)) { say("Оновлення: не вдалося з'єднатись"); g_ota.state = OTA_FAILED; return; }
  int code = http.GET();
  int len = http.getSize();
  if (code != 200 || (len > 0 && len != size)) {
    char b[96]; snprintf(b, sizeof(b), "Оновлення: HTTP %d, розмір %d замість %d", code, len, size);
    say(b); http.end(); g_ota.state = OTA_FAILED; return;
  }
  if (!Update.begin(size, U_FLASH)) {
    say("Оновлення: немає місця (потрібна OTA-розмітка — прошийте кабелем)"); http.end(); g_ota.state = OTA_FAILED; return;
  }
  if (md5.length() == 32) Update.setMD5(md5.c_str());
  WiFiClient* s = http.getStreamPtr();
  static uint8_t buf[2048];
  int done = 0;
  uint32_t lastData = millis();
  while (done < size) {
    size_t av = s->available();
    if (av) {
      int n = s->readBytes(buf, av > sizeof(buf) ? sizeof(buf) : av);
      if (n <= 0) continue;
      if (Update.write(buf, n) != (size_t)n) break;
      done += n;
      g_ota.pct = (uint8_t)((int64_t)done * 100 / size);
      lastData = millis();
    } else {
      if (!http.connected() || millis() - lastData > 20000) break;
      vTaskDelay(pdMS_TO_TICKS(5));
    }
  }
  http.end();
  if (done != size) {
    Update.abort();
    char b[96]; snprintf(b, sizeof(b), "Оновлення перервано: %d з %d байт", done, size);
    say(b); g_ota.state = OTA_FAILED; return;
  }
  if (!Update.end(true)) {
    char b[96]; snprintf(b, sizeof(b), "Оновлення відхилено: %s", Update.errorString());
    say(b); g_ota.state = OTA_FAILED; return;
  }
  say("Оновлено — перезапуск");
  g_ota.state = OTA_REBOOTING;
  vTaskDelay(pdMS_TO_TICKS(2500));
  ESP.restart();
}

void otaTick() {
  bool manual = s_req;
  if (!manual && (!g_set.autoUpdate || (int32_t)(millis() - s_next) < 0)) return;
  s_req = false;
  if (WiFi.status() != WL_CONNECTED) { s_next = millis() + OTA_RETRY_MS; return; }
  if (alertActive()) {   // ніколи не оновлюємося під час загрози місту
    s_next = millis() + OTA_RETRY_MS;
    if (manual) { say("Зараз загроза — оновлення пізніше"); g_ota.manual = false; }
    return;
  }
  g_ota.state = OTA_CHECKING;
  WiFiClientSecure sec;
  HTTPClient http;
  JsonDocument doc;
  bool ok = false;
  if (begin(http, sec, OTA_MANIFEST_URL)) {
    int code = http.GET();
    if (code == 200) ok = !deserializeJson(doc, http.getStream());
    else Serial.printf("[ota] manifest HTTP %d\n", code);
    http.end();
  }
  if (!ok) {
    s_next = millis() + OTA_RETRY_MS;
    g_ota.state = OTA_IDLE;
    if (manual) say("Не вдалося перевірити оновлення");
    g_ota.manual = false;
    return;
  }
  s_next = millis() + OTA_CHECK_MS;
  const char* ver = doc["version"] | "";
  strlcpy(g_ota.remote, ver, sizeof(g_ota.remote));
  JsonObject e = doc["env"][FW_ENV];
  String url = e["url"] | "";
  int size = e["size"] | 0;
  String md5 = e["md5"] | "";
  g_ota.state = OTA_IDLE;
  if (!ver[0] || otaCompare(ver, FW_VERSION) <= 0) {
    if (manual) { char b[96]; snprintf(b, sizeof(b), "Встановлено останню версію v%s", FW_VERSION); say(b); }
    g_ota.manual = false;
    return;
  }
  if (!url.length() || size <= 0) { say("Маніфест без файлу для цієї плати"); g_ota.manual = false; return; }
  if (alertActive()) { s_next = millis() + OTA_RETRY_MS; g_ota.manual = false; return; }
  char b[96]; snprintf(b, sizeof(b), "Завантажую v%s…", ver); say(b);
  install(url, size, md5);
  g_ota.manual = false;
  if (g_ota.state == OTA_FAILED) s_next = millis() + OTA_RETRY_MS * 3;
}
