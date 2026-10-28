#pragma once
#include "app.h"
#define TG_MAX_POSTS 10
// Завантажує https://t.me/s/<channel> і потоково витягує останні пости (до maxPosts) у хронологічному порядку.
// Повертає кількість постів або -1 при помилці.
int tgFetchChannel(const char* channel, TgPost* out, int maxPosts);
