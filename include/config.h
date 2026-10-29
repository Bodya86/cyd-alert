#pragma once
// ======================= CYD Alert — налаштування за замовчуванням =======================
// Більшість значень можна змінити з екрана "Налаштування" (зберігаються у NVS).

// Токен alerts.in.ua (отримати: https://alerts.in.ua/api-request). Порожній — використовується ubilling.
#define DEFAULT_ALERTS_TOKEN ""

// Адреса companion-сервера: динамічний (server/server.py, напр. "http://192.168.1.50:8080") або статичний
// GitHub Pages (server/build_static.py + .github/workflows/update.yml). Порожньо — ESP усе читає сам.
#define DEFAULT_SERVER_URL "https://bodya86.github.io/cyd-alert"

// Інтервали опитування (мс)
#define POLL_ALERTS_MS      20000UL   // alerts.in.ua: ліміт ~8-10 запитів/хв з IP
#define POLL_TELEGRAM_MS    45000UL   // пряме читання t.me/s/... (сторінка ~150 КБ)
// Реалтайм-сервер (Lightsail, server/rt_server.py): WebSocket-push стану (ws://<host>/ws), без нього — HTTP-опитування
// http://<host>/static/api/state/... кожні POLL_RT_MS, а якщо й він недоступний — як раніше, GitHub (адреса з налаштувань).
// Вмикається, коли в налаштуваннях стоїть стандартна адреса GitHub Pages (або адреса цього сервера). "" — вимкнено.
#ifndef RT_SERVER_HOST
#define RT_SERVER_HOST "178.105.247.133"   // Hetzner cx23 cyd-alert-rt (https://178.105.247.133.sslip.io)
#endif
#define POLL_RT_MS          4000UL
#define POLL_SERVER_MS      10000UL   // companion-сервер
#define POLL_STATIC_MS      75000UL   // хмара (гілка data через api.github.com без токена: ≤48 запитів/год; з токеном 20 с)
#define POLL_ACTIVE_MS      30000UL   // alerts.in.ua /v1/alerts/active.json (райони + тип загрози)
#define OUTAGE_POLL_MS     900000UL   // графік відключень: раз на 15 хв
#define OUTAGE_RETRY_MS    120000UL   // ... і через 2 хв після помилки

// Скільки хвилин пост вважається актуальним
#define POST_RED_TTL_MIN     15   // ракети/КАБ
#define POST_YELLOW_TTL_MIN  40   // дрони

// Часовий пояс (Київ)
#define TZ_INFO "EET-2EEST,M3.5.0/3,M10.5.0/4"

// ----------------------- Апаратні піни CYD (ESP32-2432S028R) -----------------------
#define PIN_TOUCH_CS   33
#define PIN_TOUCH_IRQ  36
#define PIN_TOUCH_MOSI 32
#define PIN_TOUCH_MISO 39
#define PIN_TOUCH_CLK  25
#define PIN_LED_R      4      // RGB LED, активний рівень LOW
#define PIN_LED_G      16
#define PIN_LED_B      17
#define PIN_SPEAKER    26     // роз'єм SPEAK (через підсилювач SC8002B) або п'єзо-бузер
#define PIN_LDR        34
#define PIN_BACKLIGHT  21

// Акумулятор (опційно): дільник 100 кОм / 100 кОм від «+» АКБ на вільний вхід ADC1.
// GPIO35 — роз'єм P3 (лише вхід, ADC1_CH7). -1 — вимкнути індикатор. Можна перевизначити через build_flags.
#ifndef BAT_ADC_PIN
#define BAT_ADC_PIN    35
#endif
#ifndef BAT_DIV
#define BAT_DIV        2.0f   // (R1 + R2) / R2
#endif
#ifndef BAT_CAL
#define BAT_CAL        1.0f   // поправка: (напруга мультиметром) / (показ на екрані)
#endif

// Калібрування тачскріна (сирі значення XPT2046). Можна перекалібрувати з меню.
#define TOUCH_X_MIN 300
#define TOUCH_X_MAX 3800
#define TOUCH_Y_MIN 250
#define TOUCH_Y_MAX 3800
