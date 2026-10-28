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

// Калібрування тачскріна (сирі значення XPT2046). Можна перекалібрувати з меню.
#define TOUCH_X_MIN 300
#define TOUCH_X_MAX 3800
#define TOUCH_Y_MIN 250
#define TOUCH_Y_MAX 3800
