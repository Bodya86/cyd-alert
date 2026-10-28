#pragma once
// Графік погодинних відключень (ГПВ): 48 півгодинних клітинок на добу
#include <stdint.h>
#include <time.h>

#define OUT_ON 0        // світло є
#define OUT_OFF 1       // світла немає
#define OUT_MAYBE 2     // можливе відключення
#define OUT_NONE 3      // немає даних

#define OUT_MODE_OFF 0
#define OUT_MODE_AUTO 1
#define OUT_MODE_MANUAL 2

struct OutageDay {
  int32_t ymd = 0;          // 20261028
  uint8_t c[48] = {0};
};

struct OutageInfo {
  uint8_t ndays = 0;
  OutageDay days[2];
  bool ok = false;
  bool manual = false;
  uint32_t at = 0;          // millis() останнього оновлення
  char src[40] = {0};       // назва джерела
  char updated[28] = {0};   // час оновлення з джерела
  char err[48] = {0};
};

// значення DTEK/OE ("yes","no","first","second","mfirst","msecond","maybe") → дві півгодини
void outageGpvCells(const char* v, uint8_t* two);
int32_t outageYmd(time_t t);                       // локальна дата YYYYMMDD
int32_t outageYmdFromDayTs(long dayTs);            // ключ дня (північ за Києвом, epoch) → YYYYMMDD
const OutageDay* outageDay(const OutageInfo& o, int32_t ymd);
// поточний стан: повертає OUT_* для моменту now; *changeAt — час наступної зміни (0 — невідомо),
// *nextVal — яким буде стан після зміни
uint8_t outageNow(const OutageInfo& o, time_t now, time_t* changeAt, uint8_t* nextVal);
// годин без світла (OFF) за добу ×2 (тобто кількість півгодинок)
int outageOffHalfHours(const OutageDay& d);
void outageFromMask(OutageInfo& o, uint64_t mask, time_t now);
