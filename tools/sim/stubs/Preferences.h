#pragma once
#include <Arduino.h>
// No-op NVS stub: the simulator sets g_set directly
class Preferences {
 public:
  bool begin(const char*, bool = false) { return true; }
  void end() {}
  void clear() {}
  String getString(const char*, const String& d) { return d; }
  uint32_t getULong(const char*, uint32_t d) { return d; }
  uint64_t getULong64(const char*, uint64_t d) { return d; }
  bool getBool(const char*, bool d) { return d; }
  int8_t getChar(const char*, int8_t d) { return d; }
  int16_t getShort(const char*, int16_t d) { return d; }
  uint8_t getUChar(const char*, uint8_t d) { return d; }
  template <class T> size_t putString(const char*, const T&) { return 1; }
  size_t putULong(const char*, uint32_t) { return 4; }
  size_t putULong64(const char*, uint64_t) { return 8; }
  size_t putBool(const char*, bool) { return 1; }
  size_t putChar(const char*, int8_t) { return 1; }
  size_t putShort(const char*, int16_t) { return 2; }
  size_t putUChar(const char*, uint8_t) { return 1; }
};
