#!/usr/bin/env python3
"""Сторож CYD Alert (незалежний від основного сервісу): systemd-таймер раз на хвилину, системний python3,
лише стандартна бібліотека, файл копіюється в /usr/local/bin/cyd-watchdog — працює, навіть якщо
/opt/cyd-alert зламано, venv зіпсовано або cyd-alert упав.

Перевіряє: unit cyd-alert active; /api/health відповідає; рушій не завис (iter_age_s);
пости Telegram надходять (tg_ok_age_s ≤ WD_INGEST_MAX_S, типово 120 с — «нічого не прочитано >2 хв»);
опційно WD_NEWPOST_MAX_S — жодного НОВОГО поста в жодному каналі довше N с (типово вимкнено: вночі тихо).
Проблема ≥2 перевірки поспіль → повідомлення в Telegram (TG_BOT_TOKEN з /etc/cyd-alert/env, чат WD_CHAT_ID,
типово 6554910913); нагадування раз на WD_REPEAT_S (30 хв); «відновлено» — коли все гаразд.
Зависання (health не відповідає / iter_age великий) ≥3 перевірки при active unit → systemctl restart
(не частіше раз на 15 хв).

    cyd-watchdog            одна перевірка (так запускає таймер)
    cyd-watchdog --dry-run  перевірка без надсилання й рестарту — друкує, що зробив би
    cyd-watchdog --test     надіслати тестове повідомлення (перевірка бота/чату)"""
import json, os, subprocess, sys, time, urllib.parse, urllib.request

ENV_FILE = os.environ.get("WD_ENV_FILE", "/etc/cyd-alert/env")
STATE = os.environ.get("WD_STATE", "/var/lib/cyd-watchdog/state.json")
UNIT = "cyd-alert"
DEF = {"WD_CHAT_ID": "6554910913", "WD_URL": "http://127.0.0.1:8080/api/health", "WD_INGEST_MAX_S": "120",
       "WD_ITER_MAX_S": "60", "WD_NEWPOST_MAX_S": "0", "WD_GRACE_S": "180", "WD_REPEAT_S": "1800",
       "WD_CONFIRM": "2", "WD_RESTART_AFTER": "3", "WD_RESTART_EVERY_S": "900"}


def load_env(path=ENV_FILE):
    env = dict(DEF)
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in DEF:
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def evaluate(unit_state, health, cfg):
    """→ (список проблем [(код, текст)], hung: bool). health — dict або Exception/None."""
    probs, hung = [], False
    if unit_state != "active":
        probs.append(("down", f"сервіс {UNIT}: {unit_state}"))
        return probs, False
    if not isinstance(health, dict):
        probs.append(("health", f"/api/health не відповідає ({health})"))
        return probs, True
    up = health.get("uptime_s") or 0
    if up < float(cfg["WD_GRACE_S"]):
        return [], False                       # щойно перезапущено — даємо прогрітись
    ia = health.get("iter_age_s")
    if ia is not None and ia > float(cfg["WD_ITER_MAX_S"]):
        probs.append(("hung", f"рушій не оновлювався {ia:.0f} с"))
        hung = True
    ok_age = health.get("tg_ok_age_s")
    lim = float(cfg["WD_INGEST_MAX_S"])
    if ok_age is None or ok_age > lim:
        what = "жодного разу" if ok_age is None else f"{ok_age / 60:.1f} хв"
        probs.append(("ingest", f"пости Telegram не надходять: останнє успішне читання — {what}"))
    np_lim = float(cfg["WD_NEWPOST_MAX_S"])
    na = health.get("new_post_age_s")
    if np_lim > 0 and na is not None and na > np_lim:
        probs.append(("quiet", f"немає нових постів {na / 60:.0f} хв"))
    return probs, hung


def step(st, probs, hung, now, cfg):
    """Машина станів. → (дії [(«send», текст) | («restart», причина)], новий стан)."""
    st = dict(st or {})
    acts = []
    codes = sorted(c for c, _ in probs)
    if probs:
        st["fails"] = st.get("fails", 0) + 1
        st.setdefault("since", now)
        st["hung_n"] = st.get("hung_n", 0) + 1 if hung else 0
        text = "; ".join(t for _, t in probs)
        if st["fails"] >= int(cfg["WD_CONFIRM"]):
            first = not st.get("alerted")
            changed = codes != st.get("codes")
            due = now - st.get("last_sent", 0) >= float(cfg["WD_REPEAT_S"])
            if first or changed or due:
                mins = (now - st["since"]) / 60
                head = "🔴 CYD Alert: проблема на сервері" if first else f"🔴 CYD Alert: досі проблема ({mins:.0f} хв)"
                acts.append(("send", f"{head}\n{text}"))
                st.update(alerted=True, last_sent=now, codes=codes)
        if hung and st["hung_n"] >= int(cfg["WD_RESTART_AFTER"]) and \
                now - st.get("last_restart", 0) >= float(cfg["WD_RESTART_EVERY_S"]):
            acts.append(("restart", text))
            st["last_restart"] = now
            st["hung_n"] = 0
    else:
        if st.get("alerted"):
            mins = (now - st.get("since", now)) / 60
            acts.append(("send", f"✅ CYD Alert: сервер знову в нормі (проблема тривала ~{mins:.0f} хв)"))
        st = {"last_restart": st.get("last_restart", 0), "ok_at": now}
    return acts, st


def unit_state():
    try:
        return subprocess.run(["systemctl", "is-active", UNIT], capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except Exception as e:  # noqa: BLE001
        return f"unknown ({e})"


def get_health(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}"


def send(cfg, text):
    tok = cfg.get("TG_BOT_TOKEN")
    if not tok:
        print("WARN: немає TG_BOT_TOKEN — не можу надіслати", file=sys.stderr)
        return False
    host = os.uname().nodename
    data = urllib.parse.urlencode({"chat_id": cfg["WD_CHAT_ID"], "text": f"{text}\n[{host}]",
                                   "disable_web_page_preview": "true"}).encode()
    for _ in range(3):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15) as r:
                return json.loads(r.read()).get("ok", False)
        except Exception as e:  # noqa: BLE001  (без URL — у ньому токен)
            print(f"WARN: надсилання не вдалось: {type(e).__name__}", file=sys.stderr)
            time.sleep(3)
    return False


def main(argv):
    cfg = load_env()
    dry = "--dry-run" in argv
    if "--test" in argv:
        ok = send(cfg, "🧪 CYD Alert watchdog: тестове повідомлення — сповіщення про збої працюють")
        print("sent" if ok else "FAILED")
        return 0 if ok else 1
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {}
    now = time.time()
    us = unit_state()
    h = get_health(cfg["WD_URL"]) if us == "active" else None
    probs, hung = evaluate(us, h, cfg)
    acts, st2 = step(st, probs, hung, now, cfg)
    print(("OK" if not probs else "PROBLEM: " + "; ".join(t for _, t in probs)) + (f" → {acts}" if acts else ""))
    if dry:
        return 0
    for kind, txt in acts:
        if kind == "send":
            send(cfg, txt)
        elif kind == "restart":
            subprocess.run(["systemctl", "restart", UNIT], timeout=60)
            send(cfg, f"♻️ CYD Alert: сервіс завис ({txt}) — перезапущено автоматично")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st2, f)
    os.replace(tmp, STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
