// Розрахунки графіка відключень (без залежностей від Arduino — тестується на ПК)
#include "outage.h"
#include <string.h>

void outageGpvCells(const char* v, uint8_t* t) {
  t[0] = t[1] = OUT_ON;
  if (!v) return;
  if (!strcmp(v, "no")) t[0] = t[1] = OUT_OFF;
  else if (!strcmp(v, "first")) t[0] = OUT_OFF;
  else if (!strcmp(v, "second")) t[1] = OUT_OFF;
  else if (!strcmp(v, "mfirst")) t[0] = OUT_MAYBE;
  else if (!strcmp(v, "msecond")) t[1] = OUT_MAYBE;
  else if (!strcmp(v, "maybe") || !strcmp(v, "mno")) t[0] = t[1] = OUT_MAYBE;
}

int32_t outageYmd(time_t t) {
  struct tm tm; localtime_r(&t, &tm);
  return (tm.tm_year + 1900) * 10000 + (tm.tm_mon + 1) * 100 + tm.tm_mday;
}

int32_t outageYmdFromDayTs(long ts) {
  // північ за Києвом = 22:00 або 21:00 UTC попереднього дня; +3 год дає ту саму дату в UTC
  time_t t = (time_t)ts + 3 * 3600;
  struct tm tm; gmtime_r(&t, &tm);
  return (tm.tm_year + 1900) * 10000 + (tm.tm_mon + 1) * 100 + tm.tm_mday;
}

const OutageDay* outageDay(const OutageInfo& o, int32_t ymd) {
  for (int i = 0; i < o.ndays; i++) if (o.days[i].ymd == ymd) return &o.days[i];
  return nullptr;
}

static time_t dayStart(time_t now) {
  struct tm tm; localtime_r(&now, &tm);
  tm.tm_hour = tm.tm_min = tm.tm_sec = 0; tm.tm_isdst = -1;
  return mktime(&tm);
}

uint8_t outageNow(const OutageInfo& o, time_t now, time_t* changeAt, uint8_t* nextVal) {
  if (changeAt) *changeAt = 0;
  if (nextVal) *nextVal = OUT_NONE;
  if (now < 100000) return OUT_NONE;
  time_t d0 = dayStart(now);
  const OutageDay* today = outageDay(o, outageYmd(now));
  if (!today) return OUT_NONE;
  struct tm tm; localtime_r(&now, &tm);
  int idx = (tm.tm_hour * 60 + tm.tm_min) / 30;
  uint8_t cur = today->c[idx];
  // шукаємо зміну сьогодні, потім завтра
  for (int i = idx + 1; i < 48; i++)
    if (today->c[i] != cur) {
      if (changeAt) *changeAt = d0 + i * 1800;
      if (nextVal) *nextVal = today->c[i];
      return cur;
    }
  time_t d1 = dayStart(d0 + 36 * 3600);
  const OutageDay* tom = outageDay(o, outageYmd(d1 + 3600));
  if (tom)
    for (int i = 0; i < 48; i++)
      if (tom->c[i] != cur) {
        if (changeAt) *changeAt = d1 + i * 1800;
        if (nextVal) *nextVal = tom->c[i];
        return cur;
      }
  return cur;
}

int outageOffHalfHours(const OutageDay& d) {
  int n = 0;
  for (int i = 0; i < 48; i++) n += d.c[i] == OUT_OFF;
  return n;
}

void outageFromMask(OutageInfo& o, uint64_t mask, time_t now) {
  o.ndays = 0;
  o.manual = true;
  if (now < 100000) return;
  time_t d0 = dayStart(now);
  time_t ds[2] = {d0 + 3600, dayStart(d0 + 36 * 3600) + 3600};
  for (int k = 0; k < 2; k++) {
    OutageDay& d = o.days[o.ndays++];
    d.ymd = outageYmd(ds[k]);
    for (int i = 0; i < 48; i++) d.c[i] = (mask >> i) & 1 ? OUT_OFF : OUT_ON;
  }
  o.ok = true;
  strncpy(o.src, "вручну", sizeof(o.src));
  o.updated[0] = 0; o.err[0] = 0;
}
