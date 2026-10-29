// Host stub of the Arduino API — only what CYD Alert UI code needs (simulator, not firmware)
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <math.h>
#include <time.h>
#ifdef __cplusplus
extern "C" {
#endif
uint32_t millis(void);
void delay(uint32_t ms);
time_t sim_time(time_t* t);
size_t sim_strlcpy(char* d, const char* s, size_t n);
#ifdef __cplusplus
}
#endif
#define strlcpy sim_strlcpy

#ifdef __cplusplus
#include <ctime>
#include <string>
#include <algorithm>
#define time(x) sim_time(x)

class String {
 public:
  std::string s;
  String() {}
  String(const char* c) : s(c ? c : "") {}
  String(const std::string& x) : s(x) {}
  String(char c) : s(1, c) {}
  String(int v) : s(std::to_string(v)) {}
  String(unsigned v) : s(std::to_string(v)) {}
  String(long v) : s(std::to_string(v)) {}
  String(unsigned long v) : s(std::to_string(v)) {}
  String(long long v) : s(std::to_string(v)) {}
  String(unsigned long long v) : s(std::to_string(v)) {}
  String(double v, int d = 2) { char b[32]; snprintf(b, sizeof b, "%.*f", d, v); s = b; }
  const char* c_str() const { return s.c_str(); }
  unsigned length() const { return (unsigned)s.size(); }
  String& operator+=(const String& o) { s += o.s; return *this; }
  String& operator+=(const char* o) { s += o ? o : ""; return *this; }
  String& operator+=(char c) { s += c; return *this; }
  String& operator+=(int v) { s += std::to_string(v); return *this; }
  bool operator==(const String& o) const { return s == o.s; }
  bool operator==(const char* o) const { return s == (o ? o : ""); }
  bool operator!=(const String& o) const { return s != o.s; }
  char operator[](unsigned i) const { return i < s.size() ? s[i] : 0; }
  void replace(const String& a, const String& b) {
    if (a.s.empty()) return;
    size_t p = 0;
    while ((p = s.find(a.s, p)) != std::string::npos) { s.replace(p, a.s.size(), b.s); p += b.s.size(); }
  }
  void trim() {
    size_t b = s.find_first_not_of(" \t\r\n"), e = s.find_last_not_of(" \t\r\n");
    s = b == std::string::npos ? "" : s.substr(b, e - b + 1);
  }
  int indexOf(const char* x) const { size_t p = s.find(x); return p == std::string::npos ? -1 : (int)p; }
  bool startsWith(const char* x) const { return s.rfind(x, 0) == 0; }
  String substring(unsigned a, unsigned b = 0xFFFFFFFF) const { return String(s.substr(a, b == 0xFFFFFFFF ? std::string::npos : b - a)); }
  int toInt() const { return atoi(s.c_str()); }
  void toLowerCase() { std::transform(s.begin(), s.end(), s.begin(), ::tolower); }
};
inline String operator+(const String& a, const String& b) { return String(a.s + b.s); }
inline String operator+(const String& a, const char* b) { return String(a.s + (b ? b : "")); }
inline String operator+(const char* a, const String& b) { return String(std::string(a ? a : "") + b.s); }
inline String operator+(const String& a, char c) { return String(a.s + c); }

struct SimSerial {
  void begin(unsigned long) {}
  int printf(const char*, ...) { return 0; }
  void println(const char* = "") {}
  void print(const char*) {}
};
extern SimSerial Serial;
struct SimEsp {
  uint32_t getFreeHeap() { return 104 * 1024; }
  void restart() {}
};
extern SimEsp ESP;
bool getLocalTime(struct tm* info, uint32_t ms = 5000);

// FreeRTOS (settings.cpp)
typedef void* SemaphoreHandle_t;
#define portMAX_DELAY 0xFFFFFFFF
inline SemaphoreHandle_t xSemaphoreCreateRecursiveMutex() { static int x; return &x; }
inline int xSemaphoreTakeRecursive(SemaphoreHandle_t, uint32_t) { return 1; }
inline int xSemaphoreGiveRecursive(SemaphoreHandle_t) { return 1; }
#endif
