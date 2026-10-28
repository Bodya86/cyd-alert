"""Нормалізація тексту постів (UA + RU) і нечіткий пошук слів. Те саме робить src/textutil.cpp (utf8Fold / fuzzyToken).

fold(): нижній регістр; і/ї/ы → и; є/э/ё → е; ґ → г; ь/ъ/апострофи — прибрати; емодзі й розділові знаки → пробіл;
лишаються літери, цифри та «@ - . /»; кілька пробілів → один; по краях — пробіл (щоб шукати початок слова як " слово").
"""
import re

_MAP = {"і": "и", "ї": "и", "ы": "и", "є": "е", "э": "е", "ё": "е", "ґ": "г", "ь": "", "ъ": "", "'": "", "ʼ": "", "’": "", "`": ""}
_KEEP = re.compile(r"[^0-9a-zа-я@\-./ ]")


def _fold_core(s):
    s = (s or "").lower()
    s = "".join(_MAP.get(c, c) for c in s)
    return _KEEP.sub(" ", s)


def fold(text):
    return " " + " ".join(_fold_core(text).split()) + " "


def fold_kw(kw):
    """Ключове слово правил: та сама заміна, але без обрізання крайніх пробілів (наприклад «каб »)."""
    s = _fold_core(kw)
    return re.sub(r" {2,}", " ", s)


def lev(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def fuzzy_allow(stem):
    n = len(stem)
    return 0 if n < 5 else (1 if n < 8 else 2)


def fuzzy_token(tok, stem):
    """«=слово» — точний збіг токена; інакше токен починається з основи з похибкою ≤ 0/1/2 (довжина <5 / 5–7 / 8+)."""
    if stem.startswith("="):
        return tok == stem[1:]
    if tok.startswith(stem):
        return True
    d = fuzzy_allow(stem)
    if not d:
        return False
    L = len(stem)
    for k in range(max(1, L - d), min(len(tok), L + d) + 1):
        if lev(tok[:k], stem, d) <= d:
            return True
    return False


def fuzzy_any(folded, stems):
    toks = folded.split()
    for s in stems:
        for t in toks:
            if fuzzy_token(t, s):
                return s
    return None
