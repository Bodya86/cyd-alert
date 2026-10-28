#include "textutil.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

static size_t putUtf8(uint32_t cp, char* o, size_t room) {
  if (cp < 0x80) { if (room < 1) return 0; o[0] = (char)cp; return 1; }
  if (cp < 0x800) { if (room < 2) return 0; o[0] = 0xC0 | (cp >> 6); o[1] = 0x80 | (cp & 0x3F); return 2; }
  if (room < 3) return 0;
  o[0] = 0xE0 | (cp >> 12); o[1] = 0x80 | ((cp >> 6) & 0x3F); o[2] = 0x80 | (cp & 0x3F); return 3;
}

size_t utf8Lower(const char* in, char* out, size_t outSize) {
  size_t o = 0;
  const uint8_t* p = (const uint8_t*)in;
  while (*p && o + 1 < outSize) {
    uint32_t cp; int len;
    if (*p < 0x80) { cp = *p; len = 1; }
    else if ((*p & 0xE0) == 0xC0 && p[1]) { cp = ((p[0] & 0x1F) << 6) | (p[1] & 0x3F); len = 2; }
    else if ((*p & 0xF0) == 0xE0 && p[1] && p[2]) { cp = ((p[0] & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F); len = 3; }
    else if ((*p & 0xF8) == 0xF0 && p[1] && p[2] && p[3]) {  // 4-byte (emoji) — копіюємо як є
      if (o + 4 >= outSize) break;
      memcpy(out + o, p, 4); o += 4; p += 4; continue;
    } else { p++; continue; }
    if (cp >= 'A' && cp <= 'Z') cp += 32;
    else if (cp >= 0x410 && cp <= 0x42F) cp += 0x20;       // А-Я
    else if (cp >= 0x400 && cp <= 0x40F) cp += 0x50;       // Ѐ-Џ (Є І Ї ...)
    else if (cp == 0x490) cp = 0x491;                      // Ґ
    if (cp == 0x451) cp = 0x435;                           // ё -> е
    if (cp == 0x2BC || cp == 0x2019 || cp == 0x60) cp = '\''; // апострофи
    if (cp == 0xA0) cp = ' ';
    size_t w = putUtf8(cp, out + o, outSize - 1 - o);
    if (!w) break;
    o += w; p += len;
  }
  out[o] = 0;
  return o;
}

static uint32_t decodeCp(const uint8_t*& p) {
  uint32_t cp;
  if (*p < 0x80) cp = *p++;
  else if ((*p & 0xE0) == 0xC0 && p[1]) { cp = ((p[0] & 0x1F) << 6) | (p[1] & 0x3F); p += 2; }
  else if ((*p & 0xF0) == 0xE0 && p[1] && p[2]) { cp = ((p[0] & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F); p += 3; }
  else if ((*p & 0xF8) == 0xF0 && p[1] && p[2] && p[3]) { cp = 0x1F600; p += 4; }   // емодзі
  else { cp = 0xFFFD; p++; }
  return cp;
}

size_t utf8Fold(const char* in, char* out, size_t outSize) {
  if (outSize < 3) { if (outSize) out[0] = 0; return 0; }
  size_t o = 0;
  out[o++] = ' ';
  const uint8_t* p = (const uint8_t*)in;
  while (*p && o + 2 < outSize) {
    uint32_t cp = decodeCp(p);
    if (cp >= 'A' && cp <= 'Z') cp += 32;
    else if (cp >= 0x410 && cp <= 0x42F) cp += 0x20;
    else if (cp >= 0x400 && cp <= 0x40F) cp += 0x50;
    else if (cp == 0x490) cp = 0x491;
    if (cp == 0x456 || cp == 0x457 || cp == 0x44B) cp = 0x438;                 // і ї ы → и
    else if (cp == 0x454 || cp == 0x44D || cp == 0x451) cp = 0x435;            // є э ё → е
    else if (cp == 0x491) cp = 0x433;                                          // ґ → г
    if (cp == 0x44C || cp == 0x44A || cp == '\'' || cp == 0x2BC || cp == 0x2019 || cp == 0x60) continue;  // ь ъ апострофи
    bool keep = (cp >= '0' && cp <= '9') || (cp >= 'a' && cp <= 'z') || (cp >= 0x430 && cp <= 0x44F) ||
                cp == '@' || cp == '-' || cp == '.' || cp == '/';
    if (!keep) { if (out[o - 1] != ' ') out[o++] = ' '; continue; }
    size_t w = putUtf8(cp, out + o, outSize - 2 - o);
    if (!w) break;
    o += w;
  }
  if (out[o - 1] != ' ') out[o++] = ' ';
  out[o] = 0;
  return o;
}

// UTF-8 → масив кодів (кирилиця/латиниця), max n
static int toCps(const char* s, size_t len, uint16_t* cp, int n) {
  const uint8_t* p = (const uint8_t*)s;
  const uint8_t* e = p + len;
  int k = 0;
  while (p < e && *p && k < n) cp[k++] = (uint16_t)decodeCp(p);
  return k;
}

static int levCap(const uint16_t* a, int na, const uint16_t* b, int nb, int cap) {
  if (na - nb > cap || nb - na > cap) return cap + 1;
  uint8_t prev[34], cur[34];
  for (int j = 0; j <= nb; j++) prev[j] = j;
  for (int i = 1; i <= na; i++) {
    cur[0] = i;
    uint8_t rowMin = cur[0];
    for (int j = 1; j <= nb; j++) {
      int v = prev[j] + 1;
      if (cur[j - 1] + 1 < v) v = cur[j - 1] + 1;
      int s = prev[j - 1] + (a[i - 1] != b[j - 1]);
      if (s < v) v = s;
      cur[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > cap) return cap + 1;
    memcpy(prev, cur, nb + 1);
  }
  return prev[nb];
}

bool fuzzyToken(const char* tok, size_t tokLen, const char* stem) {
  while (tokLen && (tok[tokLen - 1] == '.' || tok[tokLen - 1] == '-' || tok[tokLen - 1] == '/')) tokLen--;
  if (!tokLen) return false;
  if (stem[0] == '=') { size_t sl = strlen(stem + 1); return sl == tokLen && !memcmp(tok, stem + 1, sl); }
  size_t sl = strlen(stem);
  if (tokLen >= sl && !memcmp(tok, stem, sl)) return true;
  uint16_t a[32], b[32];
  int nb = toCps(stem, sl, b, 32);
  int d = nb < 5 ? 0 : (nb < 8 ? 1 : 2);
  if (!d) return false;
  int na = toCps(tok, tokLen, a, 32);
  int lo = nb - d < 1 ? 1 : nb - d, hi = na < nb + d ? na : nb + d;
  for (int k = lo; k <= hi; k++)
    if (levCap(a, k, b, nb, d) <= d) return true;
  return false;
}

const char* fuzzyAny(const char* folded, const char* const* stems, int n) {
  for (int i = 0; i < n; i++) {
    const char* p = folded;
    while (*p) {
      while (*p == ' ') p++;
      const char* s = p;
      while (*p && *p != ' ') p++;
      if (p > s && fuzzyToken(s, p - s, stems[i])) return stems[i];
    }
  }
  return nullptr;
}

bool hasWord(const char* lowered, const char* kw) { return strstr(lowered, kw) != nullptr; }

int countWords(const char* lowered, const char* const* kws, int n, const char** first) {
  int c = 0;
  for (int i = 0; i < n; i++)
    if (strstr(lowered, kws[i])) { if (!c && first) *first = kws[i]; c++; }
  return c;
}

void utf8Truncate(char* s, size_t maxBytes) {
  size_t len = strlen(s);
  if (len <= maxBytes) return;
  size_t i = maxBytes;
  while (i > 0 && ((uint8_t)s[i] & 0xC0) == 0x80) i--;
  s[i] = 0;
}

static long daysFromCivil(int y, unsigned m, unsigned d) {
  y -= m <= 2;
  const long era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = (unsigned)(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + (long)doe - 719468;
}

time_t parseIso8601(const char* s) {
  int Y, M, D, h, m, sec;
  if (sscanf(s, "%d-%d-%dT%d:%d:%d", &Y, &M, &D, &h, &m, &sec) != 6) return 0;
  long t = daysFromCivil(Y, M, D) * 86400L + h * 3600L + m * 60L + sec;
  const char* tz = s + 19;
  while (*tz && *tz != '+' && *tz != '-' && *tz != 'Z') tz++;
  if (*tz == '+' || *tz == '-') {
    int th = 0, tm = 0;
    sscanf(tz + 1, "%d:%d", &th, &tm);
    long off = th * 3600L + tm * 60L;
    t += (*tz == '+') ? -off : off;
  }
  return (time_t)t;
}

int angleDiff(int a, int b) {
  int d = abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}
