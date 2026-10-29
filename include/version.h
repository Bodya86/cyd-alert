#pragma once
// Версія прошивки. Workflow .github/workflows/firmware.yml публікує реліз v<FW_VERSION>, якщо такого ще немає —
// пристрої з увімкненим «Автооновлення» підтягнуть його (OTA). Підвищуйте версію при кожній зміні прошивки.
#define FW_VERSION "1.3.1"

#ifndef FW_ENV                 // ідентифікатор збірки (env у platformio.ini) — обирає потрібний .bin у version.json
#define FW_ENV "cyd"
#endif

// Маніфест оновлень (асет останнього релізу GitHub; редірект на objects.githubusercontent.com)
#define OTA_MANIFEST_URL "https://github.com/Bodya86/cyd-alert/releases/latest/download/version.json"
#define OTA_CHECK_MS      (6UL * 3600UL * 1000UL)   // раз на 6 год
#define OTA_FIRST_MS      (90UL * 1000UL)           // перша перевірка — через 90 с після старту
#define OTA_RETRY_MS      (10UL * 60UL * 1000UL)    // тривога/помилка — повтор через 10 хв
