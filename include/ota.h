#pragma once
#include <Arduino.h>

// Оновлення по повітрю (OTA): маніфест version.json → firmware-<env>.bin, перевірка розміру і MD5, перезапуск.
enum OtaState : uint8_t { OTA_IDLE = 0, OTA_CHECKING, OTA_DOWNLOADING, OTA_REBOOTING, OTA_FAILED };
struct OtaStatus {
  volatile uint8_t state = OTA_IDLE;
  volatile uint8_t pct = 0;
  volatile uint32_t msgSeq = 0;   // збільшується, коли є нове повідомлення для користувача (msg)
  char msg[96] = {0};
  char remote[16] = {0};          // версія з маніфесту ("" — ще не перевіряли)
  volatile bool manual = false;   // перевірку запустив користувач (показати результат тостом)
};
extern OtaStatus g_ota;

int  otaCompare(const char* a, const char* b);   // семвер: <0, 0, >0
void otaTick();            // викликається з мережевої задачі (core 0)
void otaRequestCheck();    // кнопка «Перевірити оновлення» — перевірити і встановити, якщо є нова
void otaBootCheck();       // на старті: стан слота (PENDING_VERIFY?), чи був відкат
void otaValidTick();       // з loop(): підтвердити прошивку після успішного старту (або відкотити)
bool otaPending();         // прошивка ще не підтверджена
void otaMarkValid();       // підтвердити вручну
