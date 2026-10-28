// Потоковий парсер веб-превʼю Telegram-каналу (t.me/s/<channel>) без завантаження сторінки в RAM.
#include "tgscrape.h"
#include "textutil.h"
#include "tgparser.h"
#include <HTTPClient.h>
#include <WiFiClientSecure.h>



int tgFetchChannel(const char* channel, TgPost* out, int maxPosts) {
  if (!channel || !channel[0]) return -1;
  WiFiClientSecure client;
  client.setInsecure();  // сертифікат не перевіряємо (немає місця для CA bundle) — дані лише читаються
  client.setTimeout(12);
  HTTPClient http;
  String url = String("https://t.me/s/") + channel;
  if (!http.begin(client, url)) return -1;
  http.useHTTP10(true);  // без chunked-кодування — простіше потокове читання
  http.setUserAgent("Mozilla/5.0 (CYD-Alert ESP32)");
  http.setTimeout(12000);
  int code = http.GET();
  if (code != 200) { http.end(); return -1; }
  WiFiClient* s = http.getStreamPtr();
  static tgp::Parser p;  // ~ 0.5 КБ, не на стеку
  p = tgp::Parser();
  p.out = out; p.maxPosts = maxPosts;
  uint8_t buf[512];
  uint32_t t0 = millis();
  int remaining = http.getSize();  // -1 для chunked
  while (http.connected() && (remaining > 0 || remaining == -1) && millis() - t0 < 20000) {
    size_t av = s->available();
    if (!av) { delay(2); continue; }
    int r = s->readBytes(buf, av > sizeof(buf) ? sizeof(buf) : av);
    for (int i = 0; i < r; i++) p.feed((char)buf[i]);
    if (remaining > 0) remaining -= r;
  }
  p.commit();
  http.end();
  return p.count;
}
