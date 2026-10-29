#pragma once
#include <Arduino.h>
#include <vector>
// Host stub of the ESP32 WiFi API: a scripted scan result list (simulator)
typedef enum { WL_IDLE_STATUS = 0, WL_NO_SSID_AVAIL = 1, WL_CONNECTED = 3, WL_CONNECT_FAILED = 4, WL_DISCONNECTED = 6 } wl_status_t;
enum { WIFI_OFF = 0, WIFI_STA = 1 };
enum { WIFI_AUTH_OPEN = 0, WIFI_AUTH_WPA2_PSK = 3 };
#define WIFI_SCAN_RUNNING (-1)
#define WIFI_SCAN_FAILED (-2)
struct IPAddress {
  uint8_t a[4];
  String toString() const { char b[20]; snprintf(b, sizeof b, "%u.%u.%u.%u", a[0], a[1], a[2], a[3]); return String(b); }
};
struct SimNet { String ssid; int rssi; int ch; int enc; };
class SimWiFi {
 public:
  std::vector<SimNet> nets;
  wl_status_t st = WL_CONNECTED;
  bool scanned = false;
  wl_status_t status() { return st; }
  void mode(int) {}
  void disconnect(bool = false, bool = false) {}
  void setAutoReconnect(bool) {}
  void begin(const char*, const char*) {}
  int16_t scanNetworks(bool, bool) { scanned = true; return WIFI_SCAN_RUNNING; }
  int16_t scanComplete() { return scanned ? (int16_t)nets.size() : WIFI_SCAN_FAILED; }
  void scanDelete() {}
  String SSID(int i) { return nets[i].ssid; }
  int32_t RSSI(int i) { return nets[i].rssi; }
  int32_t channel(int i) { return nets[i].ch; }
  int encryptionType(int i) { return nets[i].enc; }
  IPAddress localIP() { return IPAddress{{192, 168, 31, 57}}; }
};
extern SimWiFi WiFi;
