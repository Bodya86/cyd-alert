#pragma once
#include "app.h"

#define POST_OBLAST_TTL_MIN 10
#define FAR_PLACE_KM 150
struct PostEval {
  int score = 0;
  Level level = LVL_GREEN;    // ЖОВТИЙ = дрони/невідома ціль, ЧЕРВОНИЙ = ракети/КАБ/авіація
  bool near = false;          // бал >= 7: ціль у вашому районі / курс на вас
  const char* kind = "";      // "Дрони", "Ракети", "КАБ", ...
  bool clear = false;     // пост про відбій / ціль пролетіла
  bool ignored = false;   // реклама / нерелевантне
  bool relevant = false;  // є хоч якась згадка загроз
  bool far = false;       // усі згадані місця далі 150 км і не до нас — лише інформація (рівень не підвищується)
  const char* farPlace = "";
  int farKm = 0;
  bool oblastWide = false; // балістика без місця / «хз звідки» — червоний на всю область на POST_OBLAST_TTL_MIN
  char reason[120] = {0};
};

// Оцінка одного посту для міста/району користувача (district = -1 — все місто)
PostEval analyzePost(const char* text, const CityDef* city, int district);

// Підсумок по стрічці постів (у хронологічному порядку). now — поточний UTC epoch (0 = невідомо).
// Повертає рівень і індекс посту, що його визначив (-1 якщо немає).
Level analyzeFeed(const TgPost* posts, int n, const CityDef* city, int district, time_t now,
                  int* bestIdx, PostEval* bestEval);
