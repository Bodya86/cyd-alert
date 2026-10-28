"""Мінімальний WebSocket-клієнт (лише stdlib) для JAAM data_fusion_v1.

Протокол: https://info.jaam.net.ua/fusion/api/server-api/  (дані: JAAM, jaam.net.ua)
* після з'єднання шлемо chip_id:<стабільний id> і firmware:<версія>;
* сервер шле бінарні кадри: 0xA1 тривоги (region_id u16, flags u16, з хеш-ланцюжком),
  0xA2 разові сповіщення (дрон/ракета/КАБ/балістика/вибух) по регіону;
* на ping відповідаємо pong; перепідключення з експоненційною затримкою 4…128 с ± 20 %.
"""
import base64, hashlib, os, random, socket, ssl, struct, threading, time, uuid

ALERT_FLAGS = {0: "air", 1: "artillery", 2: "urban_fights", 3: "chemical", 4: "nuclear",
               5: "drones", 6: "missile", 11: "level_yellow", 12: "level_red"}
NOTIFY_FLAGS = {5: "drone", 6: "rocket", 7: "kab", 8: "ballistic", 9: "explosion", 10: "recon_drone"}

HOST, PORT, PATH = "ws.jaam.net.ua", 443, "/data_fusion_v1"
FIRMWARE = "cyd-alert-server-1.2"


def chip_id():
    """Стабільний id цього сервера (MAC/хост → sha1), як вимагає JAAM."""
    h = hashlib.sha1(f"{uuid.getnode():012X}-{socket.gethostname()}".encode()).hexdigest().upper()
    return "CYD" + h[:9]


def decode_flags(f, table):
    return [n for b, n in table.items() if f >> b & 1]


class JaamState:
    def __init__(self):
        self.alerts = {}          # region_id -> flags
        self.last_hash = None
        self.events = []          # [(ts, region_id, [types])]
        self.connected = False
        self.updated = 0.0
        self.lock = threading.Lock()

    def feed(self, msg, now=None):
        """Повертає False, якщо пропущено пакет тривог (треба перепідключитися)."""
        now = now or time.time()
        if not msg:
            return True
        kind, body = msg[0], msg[1:]
        try:
            if kind == 0xA1:
                ha, hp = struct.unpack("<HH", body[:4])
                rec = body[4:]
                if len(rec) % 4:
                    return True
                with self.lock:
                    if self.last_hash is not None and hp != self.last_hash:
                        return False
                    for rid, fl in struct.iter_unpack("<HH", rec):
                        if fl:
                            self.alerts[rid] = fl
                        else:
                            self.alerts.pop(rid, None)
                    self.last_hash = ha
                    self.updated = now
            elif kind == 0xA2:
                if len(body) % 4:
                    return True
                with self.lock:
                    for rid, fl in struct.iter_unpack("<HH", body):
                        self.events.append((now, rid, decode_flags(fl, NOTIFY_FLAGS)))
                    self.events = [e for e in self.events if now - e[0] < 3600][-500:]
        except struct.error:
            pass
        return True

    def snapshot(self):
        with self.lock:
            return dict(self.alerts), list(self.events), self.updated


def _recv_exact(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("closed")
        b += c
    return b


def _send_frame(s, op, payload=b""):
    mask = os.urandom(4)
    hdr = bytes([0x80 | op])
    n = len(payload)
    if n < 126:
        hdr += bytes([0x80 | n])
    elif n < 65536:
        hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
    s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _recv_frame(s):
    b0, b1 = _recv_exact(s, 2)
    op, n = b0 & 0x0F, b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _recv_exact(s, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _recv_exact(s, 8))[0]
    mask = _recv_exact(s, 4) if b1 & 0x80 else None
    data = _recv_exact(s, n) if n else b""
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return op, data


def run_session(state, stop):
    raw = socket.create_connection((HOST, PORT), timeout=60)
    s = ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: {FIRMWARE}\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        c = s.recv(1024)
        if not c:
            raise ConnectionError("handshake")
        resp += c
    if b" 101 " not in resp.split(b"\r\n")[0]:
        raise ConnectionError(resp.split(b"\r\n")[0].decode(errors="replace"))
    _send_frame(s, 1, f"chip_id:{chip_id()}".encode())
    _send_frame(s, 1, f"firmware:{FIRMWARE}".encode())
    state.connected = True
    state.last_hash = None
    s.settimeout(90)   # сервер пінгує кожні 20 с
    frag = b""
    try:
        while not stop.is_set():
            op, data = _recv_frame(s)
            if op == 9:
                _send_frame(s, 10, data)
            elif op == 8:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 0
                raise ConnectionError(f"close {code}")
            elif op in (1, 2, 0):
                frag += data
                if op == 2 or op == 0:
                    if not state.feed(frag):
                        raise ConnectionError("missed alerts packet")
                frag = b""
    finally:
        state.connected = False
        try:
            s.close()
        except OSError:
            pass


def run_forever(state, stop=None, log=print):
    stop = stop or threading.Event()
    delay = 4
    while not stop.is_set():
        t0 = time.monotonic()
        err = ""
        try:
            run_session(state, stop)
        except Exception as e:  # noqa: BLE001 — мережа, будь-що
            err = str(e)
        if time.monotonic() - t0 >= 60:
            delay = 4
        if "1008" in err:
            delay = 128
        wait = min(128, round(delay * random.uniform(0.8, 1.2) / 2) * 2)
        log(f"[jaam] з'єднання закрито ({err or 'ok'}), повтор через {wait} с")
        stop.wait(wait)
        delay = min(delay * 2, 128)


def start(state, log=print):
    t = threading.Thread(target=run_forever, args=(state, None, log), daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    st = JaamState()
    ev = threading.Event()
    th = threading.Thread(target=lambda: run_session(st, ev), daemon=True)
    th.start()
    time.sleep(float(os.environ.get("T", "25")))
    a, e, u = st.snapshot()
    print("alerts:", len(a), sorted((k, decode_flags(v, ALERT_FLAGS)) for k, v in a.items())[:60])
    print("events:", e[:20])
