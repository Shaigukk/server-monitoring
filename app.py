# -*- coding: utf-8 -*-
"""Учебный прототип «Мониторинг сервера».

Один процесс читает показатели и отдаёт страницу. Браузеры получают
уже собранный снимок и не запускают повторное чтение дисков.
"""

import json
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

import psutil

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"
LOG_PATH = ROOT / "monitor.log"

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8080,
    "poll_seconds": 5,
    "log_lines": 40,
    "thresholds": {"cpu": 85, "memory": 85, "disk": 90},
}

FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

LOCK = threading.RLock()
STOP = threading.Event()
CONFIG = dict(DEFAULTS)
SNAPSHOT = None
LAST_SAMPLE = None
PREVIOUS = set()


def now_text():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def clamp_int(value, low, high, fallback):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(high, max(low, number))


def load_config():
    raw = {}
    source = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    if source.exists():
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    thresholds = raw.get("thresholds") or {}
    base = DEFAULTS["thresholds"]
    config = {
        "host": str(raw.get("host") or DEFAULTS["host"]),
        "port": clamp_int(raw.get("port"), 1, 65535, DEFAULTS["port"]),
        "poll_seconds": clamp_int(raw.get("poll_seconds"), 2, 300, DEFAULTS["poll_seconds"]),
        "log_lines": clamp_int(raw.get("log_lines"), 10, 200, DEFAULTS["log_lines"]),
        "thresholds": {
            "cpu": clamp_int(thresholds.get("cpu"), 1, 100, base["cpu"]),
            "memory": clamp_int(thresholds.get("memory"), 1, 100, base["memory"]),
            "disk": clamp_int(thresholds.get("disk"), 1, 100, base["disk"]),
        },
    }
    if not CONFIG_PATH.exists():
        save_config(config)
    return config


def save_config(config):
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(CONFIG_PATH)


def append_log(kind, text):
    line = "%s  %-12s %s\n" % (now_text(), kind, text)
    with LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)


def tail_log(limit):
    if not LOG_PATH.exists():
        return []
    with LOCK:
        try:
            size = LOG_PATH.stat().st_size
            with LOG_PATH.open("rb") as handle:
                handle.seek(max(0, size - 65536))
                blob = handle.read().decode("utf-8", "replace")
        except OSError:
            return []
    lines = [line.rstrip() for line in blob.splitlines() if line.strip()]
    return lines[-limit:]


def local_client(host):
    if host in ("localhost", "::1"):
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def gigabytes(value):
    if value is None:
        return None
    return round(value / (1024 ** 3), 1)


def read_sample():
    """Один проход по системе. Медленные вызовы идут без блокировки снимка."""
    sample = {
        "taken_at": now_text(),
        "hostname": socket.gethostname(),
        "cpu": None,
        "memory": None,
        "disks": [],
        "errors": [],
    }
    try:
        sample["cpu"] = round(psutil.cpu_percent(interval=None), 1)
    except Exception as error:
        sample["errors"].append("процессор: %s" % error)

    try:
        memory = psutil.virtual_memory()
        sample["memory"] = {
            "percent": round(memory.percent, 1),
            "used_gb": gigabytes(memory.used),
            "free_gb": gigabytes(memory.available),
            "total_gb": gigabytes(memory.total),
        }
    except Exception as error:
        sample["errors"].append("память: %s" % error)

    seen = set()
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception as error:
        partitions = []
        sample["errors"].append("диски: %s" % error)

    for part in partitions:
        opts = (part.opts or "").lower()
        if "cdrom" in opts or "remote" in opts:
            continue
        mount = part.mountpoint or ""
        if len(mount) < 2 or mount[1] != ":":
            continue
        letter = mount[0].upper()
        if letter in seen:
            continue
        seen.add(letter)
        try:
            usage = psutil.disk_usage(mount)
        except OSError as error:
            # Пустой картридер отвечает ошибкой на каждый опрос и в список не входит.
            if "removable" in opts:
                continue
            sample["disks"].append({
                "letter": letter,
                "percent": None,
                "error": str(error),
            })
            sample["errors"].append("диск %s: %s" % (letter, error))
            continue
        sample["disks"].append({
            "letter": letter,
            "percent": round(usage.percent, 1),
            "used_gb": gigabytes(usage.used),
            "free_gb": gigabytes(usage.free),
            "total_gb": gigabytes(usage.total),
            "error": None,
        })
    sample["disks"].sort(key=lambda item: item["letter"])
    return sample


def alerts_for(sample, thresholds):
    found = {}
    cpu = sample.get("cpu")
    if cpu is not None and cpu > thresholds["cpu"]:
        found["cpu"] = "Загрузка процессора %s%%, порог %s%%" % (cpu, thresholds["cpu"])
    memory = sample.get("memory") or {}
    if memory.get("percent") is not None and memory["percent"] > thresholds["memory"]:
        found["memory"] = "Память занята на %s%%, порог %s%%" % (
            memory["percent"], thresholds["memory"]
        )
    for disk in sample.get("disks") or []:
        if disk.get("percent") is not None and disk["percent"] > thresholds["disk"]:
            found["disk:" + disk["letter"]] = "Диск %s заполнен на %s%%, порог %s%%" % (
                disk["letter"], disk["percent"], thresholds["disk"]
            )
    return found


def recovered_text(key, sample):
    if key == "cpu" and sample.get("cpu") is not None:
        return "Процессор %s%%, ниже порога" % sample["cpu"]
    if key == "memory":
        percent = (sample.get("memory") or {}).get("percent")
        if percent is not None:
            return "Память %s%%, ниже порога" % percent
    if key.startswith("disk:"):
        letter = key.split(":", 1)[1]
        for disk in sample.get("disks") or []:
            if disk["letter"] == letter and disk.get("percent") is not None:
                return "Диск %s %s%%, ниже порога" % (letter, disk["percent"])
    return "Показатель вернулся в норму"


def record_transitions(current, previous, sample):
    for key in sorted(set(current) - previous):
        append_log("оповещение", current[key])
    for key in sorted(previous - set(current)):
        append_log("норма", recovered_text(key, sample))


def publish(sample, poll=True):
    global SNAPSHOT, LAST_SAMPLE, PREVIOUS
    with LOCK:
        thresholds = dict(CONFIG["thresholds"])
        limit = CONFIG["log_lines"]
    current = alerts_for(sample, thresholds)
    with LOCK:
        previous = set(PREVIOUS)
    if poll:
        cpu = sample["cpu"] if sample["cpu"] is not None else "н/д"
        memory = (sample.get("memory") or {}).get("percent")
        memory_text = memory if memory is not None else "н/д"
        disks = " ".join(
            "%s:%s" % (disk["letter"], disk["percent"] if disk["percent"] is not None else "н/д")
            for disk in sample["disks"]
        ) or "нет"
        append_log("опрос", "ЦП %s%%, память %s%%, диски %s" % (cpu, memory_text, disks))
        for error in sample["errors"]:
            append_log("ошибка", error)
    record_transitions(current, previous, sample)
    with LOCK:
        PREVIOUS = set(current)
        LAST_SAMPLE = sample
        SNAPSHOT = {
            "ready": True,
            "taken_at": sample["taken_at"],
            "hostname": sample["hostname"],
            "poll_seconds": CONFIG["poll_seconds"],
            "thresholds": dict(CONFIG["thresholds"]),
            "cpu": sample["cpu"],
            "memory": sample["memory"],
            "disks": sample["disks"],
            "alerts": [{"id": key, "text": current[key]} for key in sorted(current)],
            "errors": sample["errors"],
            "log": tail_log(limit),
        }


def apply_thresholds(payload):
    if not isinstance(payload, dict):
        raise ValueError("ожидался объект с порогами")
    source = payload.get("thresholds") if isinstance(payload.get("thresholds"), dict) else payload
    with LOCK:
        current = dict(CONFIG["thresholds"])
        stored = dict(CONFIG)
    updated = {
        "cpu": clamp_int(source.get("cpu"), 1, 100, current["cpu"]),
        "memory": clamp_int(source.get("memory"), 1, 100, current["memory"]),
        "disk": clamp_int(source.get("disk"), 1, 100, current["disk"]),
    }
    if updated == current:
        return updated
    stored["thresholds"] = updated
    save_config(stored)
    with LOCK:
        CONFIG.clear()
        CONFIG.update(stored)
    append_log(
        "настройка",
        "Пороги: процессор %s%%, память %s%%, диск %s%%" % (
            updated["cpu"], updated["memory"], updated["disk"]
        ),
    )
    if LAST_SAMPLE is not None:
        publish(LAST_SAMPLE, poll=False)
    return updated


def collector():
    # Первый вызов только запускает счётчик. Осмысленное значение будет следующим.
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass
    time.sleep(0.4)
    while not STOP.is_set():
        started = time.monotonic()
        try:
            publish(read_sample(), poll=True)
        except Exception as error:
            append_log("ошибка", "опрос не выполнен: %s" % error)
        with LOCK:
            pause = CONFIG["poll_seconds"]
        STOP.wait(max(0.2, pause - (time.monotonic() - started)))


class Handler(BaseHTTPRequestHandler):
    server_version = "Monitor/1.0"

    def log_message(self, fmt, *args):
        code = str(args[1]) if len(args) > 1 else ""
        if code.startswith("200"):
            return
        super().log_message(fmt, *args)

    def send_body(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, payload):
        self.send_body(
            code,
            json.dumps(payload, ensure_ascii=False),
            "application/json; charset=utf-8",
        )

    def do_GET(self):
        if not local_client(self.client_address[0]):
            self.send_json(403, {"error": "Доступ только из локальной сети"})
            return
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                payload = SNAPSHOT
            if payload is None:
                self.send_json(200, {"ready": False, "log": tail_log(40)})
            else:
                self.send_json(200, payload)
            return
        item = FILES.get(path)
        if item is None:
            self.send_json(404, {"error": "Не найдено"})
            return
        try:
            self.send_body(200, (ROOT / item[0]).read_bytes(), item[1])
        except OSError:
            self.send_json(404, {"error": "Файл не найден"})

    def do_POST(self):
        if not local_client(self.client_address[0]):
            self.send_json(403, {"error": "Доступ только из локальной сети"})
            return
        if urlparse(self.path).path != "/api/settings":
            self.send_json(404, {"error": "Не найдено"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 8192:
            self.send_json(400, {"error": "Пустое или слишком большое тело запроса"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            updated = apply_thresholds(payload)
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            self.send_json(400, {"error": "Пороги не прочитаны: %s" % error})
            return
        self.send_json(200, {"ok": True, "thresholds": updated})


def lan_addresses():
    found = []
    for info in psutil.net_if_addrs().values():
        for item in info:
            if item.family != socket.AF_INET or not item.address:
                continue
            if local_client(item.address) and not item.address.startswith("127."):
                found.append(item.address)
    return sorted(set(found))


def serve(port=None):
    global CONFIG
    CONFIG = load_config()
    if port is not None:
        CONFIG["port"] = clamp_int(port, 1, 65535, CONFIG["port"])
    threading.Thread(target=collector, name="collector", daemon=True).start()
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((CONFIG["host"], CONFIG["port"]), Handler)
    addresses = lan_addresses()
    print("Мониторинг сервера запущен.")
    print("На этом компьютере: http://127.0.0.1:%s" % CONFIG["port"])
    if addresses:
        for address in addresses:
            print("В локальной сети:     http://%s:%s" % (address, CONFIG["port"]))
    else:
        print("Адрес локальной сети не найден. Откройте страницу с этого компьютера.")
    print("Остановка: Ctrl+C. Журнал дописывается в monitor.log.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено. Журнал сохранён.")
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Учебный мониторинг сервера")
    parser.add_argument("--port", type=int, help="перекрывает порт из config.json")
    serve(parser.parse_args().port)
