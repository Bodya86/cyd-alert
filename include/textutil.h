#pragma once
#include <stddef.h>
#include <stdint.h>
#include <time.h>
// Нижній регістр для UTF-8 (ASCII + кирилиця), ё->е, ʼ/’ -> '
size_t utf8Lower(const char* in, char* out, size_t outSize);
// Нормалізація для аналізу (= server/textnorm.py fold): нижній регістр, і/ї/ы→и, є/э/ё→е, ґ→г, без ь/ъ/апострофів,
// емодзі/розділові → пробіл, пробіли злиті, по краях — пробіл
size_t utf8Fold(const char* in, char* out, size_t outSize);
// Нечіткий збіг слова з основою: «=слово» — точний; інакше початок слова з похибкою ≤0/1/2 (основа <5 / 5–7 / 8+ літер)
bool fuzzyToken(const char* tok, size_t tokLen, const char* stem);
// Перша основа, що збіглася з будь-яким словом тексту (nullptr — немає)
const char* fuzzyAny(const char* folded, const char* const* stems, int n);
bool hasWord(const char* lowered, const char* kw);
int countWords(const char* lowered, const char* const* kws, int n, const char** first = nullptr);
// Обрізати рядок до межі символу UTF-8
void utf8Truncate(char* s, size_t maxBytes);
// "2026-10-28T18:57:02+00:00" -> epoch UTC
time_t parseIso8601(const char* s);
int angleDiff(int a, int b);
