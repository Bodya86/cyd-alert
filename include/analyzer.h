#pragma once
#include "app.h"

#define POST_OBLAST_TTL_MIN 10
#define POST_AGE_STEP_MIN 10   // дрон рухається: кожні 10 хв без нових постів рівень падає на щабель (не нижче жовтого)
#define CLS_DRONE 1
#define CLS_MISSILE 2
#define FAR_PLACE_KM 150
#define NEAR_PLACE_KM 60
#define POST_EXPLICIT_TTL_MIN 20   // пост «курс на ваше місто» дає помаранчевий/червоний без офіційної тривоги не довше 20 хв
struct PostEval {
  int score = 0;
  Level level = LVL_GREEN;    // рівень близькості (див. levelOf)
  uint8_t cls = 0;            // клас загрози: CLS_DRONE / CLS_MISSILE (0 — немає)
  uint8_t prox = 0;           // 0 — область, 1 — місто/≤15 км/курс на місто, 2 — ваш район / вибухи в місті
  bool near = false;          // бал >= 7: ціль у вашому районі / курс на вас
  const char* kind = "";      // "Дрони", "Ракети", "КАБ", ...
  bool clear = false;     // пост про відбій / ціль пролетіла
  bool ignored = false;   // реклама / нерелевантне
  bool relevant = false;  // є хоч якась згадка загроз
  bool far = false;       // усі згадані місця далі 150 км і не до нас — лише інформація (рівень не підвищується)
  const char* farPlace = "";
  int farKm = 0;
  bool local = false;      // згадано ваше місто/район/село/область або місце ближче 60 км
  uint8_t threat = 255;    // індекс у THREATS (255 — немає)
  bool explicitCity = false; // пост прямо про ваше місто/район/≤15 км або балістика на область (див. capLevel)
  bool oblastWide = false; // балістика без місця / «хз звідки» — червоний на всю область на POST_OBLAST_TTL_MIN
  char reason[120] = {0};
};

Level levelOf(uint8_t cls, uint8_t prox, bool oblastWide, bool nearby);
Level agedLevel(const PostEval& e, long ageMin);
// Оцінка одного посту для міста/району користувача (district = -1 — все місто)
// remote = true: загальнонаціональний або чужий обласний канал — без згадки вашої території пост лише інформаційний
// home = true: міський канал саме цього міста — лише тоді «по нам / над містом» означає ваше місто
PostEval analyzePost(const char* text, const CityDef* city, int district, bool remote = false, int home = -1);
// Канал — міський канал саме цього міста (kremen_sv для Кременчука)?
bool channelIsHome(const char* channel, const CityDef* city);
// Без офіційної тривоги в області і без свіжого прямого посту про ваше місто — не вище жовтого
inline Level capLevel(Level l, bool oblAlert, bool explicitCity) {
  return (oblAlert || explicitCity || l <= LVL_YELLOW) ? l : LVL_YELLOW;
}
// Канал «ваш» (міський канал вашого міста або обласний канал вашої області)?
bool channelIsLocal(const char* channel, const CityDef* city);

// Підсумок по стрічці постів (у хронологічному порядку). now — поточний UTC epoch (0 = невідомо).
// Повертає рівень і індекс посту, що його визначив (-1 якщо немає).
Level analyzeFeed(const TgPost* posts, int n, const CityDef* city, int district, time_t now,
                  int* bestIdx, PostEval* bestEval, bool remote = false, int home = -1);
