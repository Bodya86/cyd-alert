#pragma once
#include <stdint.h>

struct WordList { const char* const* w; uint8_t n; };
struct OblastDef { const char* name; const char* shortName; uint8_t uid; const char* ubilling; };
struct ThreatDef { const char* type; const char* label; const char* kind; uint8_t weight; uint8_t level; const char* const* kw; uint8_t nkw;
                   const char* const* fz; uint8_t nfz; };  // fz — нечіткі основи («=слово» — точний збіг слова)
struct PlaceDef { const char* name; float lat, lon; const char* const* kw; uint8_t nkw; };   // великі міста UA/RU назвами  // level: 1 жовтий, 2 червоний
struct OutageProvDef { const char* id; const char* name; uint8_t type; const char* url; uint32_t oblMask; };  // type: 0 gpv, 1 yasno
struct DistrictDef { const char* id; const char* name; int16_t sector; const char* const* kw; uint8_t nkw; };
struct SettlementDef { const char* const* kw; uint8_t nkw; int16_t bearing; int16_t km; };
struct StreetDef { const char* name; uint8_t district; };
struct CityDef {
  const char* id; const char* name; uint8_t oblast; float lat, lon; const char* channel;
  const char* const* kw; uint8_t nkw;
  const DistrictDef* districts; uint8_t ndist;
  const SettlementDef* settl; uint8_t nsettl;
  const StreetDef* streets; uint16_t nstreets;
  int16_t region;   // індекс району в MAP_REGIONS (data/city_regions.json), -1 — невідомо
};

// Telegram-канал (data/channels.json): scope 0 — уся Україна, 1 — область(і) з obMask, 2 — місто home
struct ChannelDef { const char* id; const char* name; uint8_t scope; uint32_t obMask; const char* home; uint8_t weight; uint8_t def; };
