#pragma once
// Потоковий парсер HTML веб-превʼю Telegram (t.me/s/<channel>). Без залежностей від Arduino — тестується на ПК.
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include "textutil.h"
#ifndef TG_TEXT_MAX
#define TG_TEXT_MAX 360
struct TgPost { uint32_t id = 0; time_t ts = 0; char text[TG_TEXT_MAX] = {0}; };
#endif

namespace tgp {
struct Matcher {
  const char* pat; uint8_t len; uint8_t pos = 0;
  Matcher(const char* p) : pat(p), len(strlen(p)) {}
  bool feed(char c) {
    if (c == pat[pos]) { if (++pos == len) { pos = 0; return true; } }
    else pos = (c == pat[0]) ? 1 : 0;
    return false;
  }
};

enum St { SCAN, POSTID, TEXT_TAG_OPEN, TEXT, TAG, ENTITY, TIME };

struct Parser {
  TgPost* out; int maxPosts; int count = 0;
  TgPost cur; bool haveCur = false, gotText = false;
  St st = SCAN;
  Matcher mPost{"data-post=\""}, mText{"js-message_text"}, mTime{"<time datetime=\""};
  char tmp[40]; uint8_t tl = 0;
  char tag[8]; uint8_t tagl = 0;
  int depth = 0; size_t textLen = 0;

  void commit() {
    if (!haveCur) return;
    utf8Truncate(cur.text, TG_TEXT_MAX - 1);
    if (cur.text[0] && cur.ts) {
      if (count < maxPosts) out[count++] = cur;
      else { memmove(out, out + 1, sizeof(TgPost) * (maxPosts - 1)); out[maxPosts - 1] = cur; }
    }
    haveCur = false;
  }
  void putc_(char c) {
    if (textLen + 1 < TG_TEXT_MAX) { cur.text[textLen++] = c; cur.text[textLen] = 0; }
  }
  void putSpace() { if (textLen && cur.text[textLen - 1] != ' ') putc_(' '); }

  void feed(char c) {
    switch (st) {
      case SCAN:
        if (mPost.feed(c)) { commit(); cur = TgPost(); haveCur = true; gotText = false; textLen = 0; tl = 0; st = POSTID; }
        else if (haveCur && !gotText && mText.feed(c)) st = TEXT_TAG_OPEN;
        else if (haveCur && mTime.feed(c)) { tl = 0; st = TIME; }
        break;
      case POSTID:
        if (c == '"') { tmp[tl] = 0; const char* sl = strrchr(tmp, '/'); cur.id = sl ? atol(sl + 1) : 0; st = SCAN; }
        else if (tl < sizeof(tmp) - 1) tmp[tl++] = c;
        break;
      case TEXT_TAG_OPEN:
        if (c == '>') { st = TEXT; depth = 0; gotText = true; }
        break;
      case TEXT:
        if (c == '<') { tagl = 0; st = TAG; }
        else if (c == '&') { tl = 0; st = ENTITY; }
        else if (c == '\n' || c == '\r' || c == '\t') putSpace();
        else putc_(c);
        break;
      case TAG:
        if (c == '>') {
          tag[tagl] = 0;
          if (!strncmp(tag, "br", 2)) putSpace();
          else if (!strncmp(tag, "div", 3)) depth++;
          else if (!strncmp(tag, "/div", 4)) { if (depth == 0) { st = SCAN; break; } depth--; }
          st = TEXT;
        } else if (tagl < sizeof(tag) - 1 && c != ' ') tag[tagl++] = c;
        else if (c == ' ') tagl = sizeof(tag) - 1;  // далі атрибути
        break;
      case ENTITY:
        if (c == ';' || tl >= 9) {
          tmp[tl] = 0;
          if (!strcmp(tmp, "amp")) putc_('&');
          else if (!strcmp(tmp, "lt")) putc_('<');
          else if (!strcmp(tmp, "gt")) putc_('>');
          else if (!strcmp(tmp, "quot")) putc_('"');
          else if (!strcmp(tmp, "nbsp")) putSpace();
          else if (tmp[0] == '#') {
            uint32_t cp = tmp[1] == 'x' ? strtoul(tmp + 2, nullptr, 16) : strtoul(tmp + 1, nullptr, 10);
            if (cp < 0x80) putc_((char)cp);
            else if (cp < 0x800) { putc_(0xC0 | (cp >> 6)); putc_(0x80 | (cp & 0x3F)); }
            else if (cp < 0x10000) { putc_(0xE0 | (cp >> 12)); putc_(0x80 | ((cp >> 6) & 0x3F)); putc_(0x80 | (cp & 0x3F)); }
          }
          st = TEXT;
        } else tmp[tl++] = c;
        break;
      case TIME:
        if (c == '"') { tmp[tl] = 0; cur.ts = parseIso8601(tmp); st = SCAN; }
        else if (tl < sizeof(tmp) - 1) tmp[tl++] = c;
        break;
    }
  }
};
}  // namespace tgp
