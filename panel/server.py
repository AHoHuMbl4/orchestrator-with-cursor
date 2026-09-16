#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP-панель параметров оркестрации — редактор params.json/compass.md + просмотр
прогонов. python3.6+, только stdlib, кроссплатформенно (Linux/macOS/Windows).

Запуск:  python3 server.py [--host 127.0.0.1] [--port 8765]
По умолчанию host/port берутся из .orchestration/params.json (секция panel).

Панель — ТОЛЬКО редактор и наблюдатель: она не запускает задачи и не принимает
решений (решения — за оркестратором). Наружу не выставлять: доступ извне —
через ssh-туннель или tailscale.
"""
import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import orchlib  # noqa: E402

PANEL_DIR = os.path.dirname(os.path.abspath(__file__))
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class Handler(BaseHTTPRequestHandler):
    server_version = "orch-panel/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---------- helpers ----------
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8")), None
        except Exception as e:
            return None, "bad json: %s" % e

    def tail_file(self, path, lines=200, chunk=8192):
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                data = b""
                while size > 0 and data.count(b"\n") <= lines:
                    step = min(chunk, size)
                    size -= step
                    f.seek(size)
                    data = f.read(step) + data
                text = data.decode("utf-8", "replace")
                return "\n".join(text.splitlines()[-lines:])
        except Exception as e:
            return "(ошибка чтения: %s)" % e

    def runs_status(self):
        state = orchlib.find_state_dir()
        out = []
        if not os.path.isdir(state):
            return out
        for name in sorted(os.listdir(state)):
            if not (name.startswith("cursor-run-") or name.startswith("cloud-")):
                continue
            if not name.endswith(".log"):
                continue
            path = os.path.join(state, name)
            pid_name = name.replace(".log", ".pid")
            pid_path = os.path.join(state, pid_name)
            running = False
            if os.path.exists(pid_path):
                try:
                    with open(pid_path, "r") as f:
                        pid = int(f.read().strip())
                    os.kill(pid, 0)
                    running = True
                except Exception:
                    running = False
            tail = self.tail_file(path, 1)
            exit_code = None
            m = re.search(r"EXIT=(-?\d+)", tail or "")
            if m:
                exit_code = int(m.group(1))
            st = os.stat(path)
            import datetime
            out.append({
                "name": name, "size": st.st_size,
                "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S"),
                "running": running, "exit": exit_code,
            })
        return out[::-1]

    # ---------- routes ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/" or u.path == "/index.html":
            with open(os.path.join(PANEL_DIR, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/params":
            self.send_json({"params": orchlib.load_params()})
        elif u.path == "/api/compass":
            p = orchlib.load_params()
            self.send_json({"path": orchlib.compass_path(p),
                            "text": orchlib.read_compass(p, limit=200000)})
        elif u.path == "/api/discovered":
            dj = orchlib.state_path("discovered.json")
            if os.path.exists(dj):
                with open(dj, "r", encoding="utf-8") as f:
                    self.send_json(json.load(f))
            else:
                self.send_json({"error": "discovered.json нет — запусти bin/discover.py"}, 404)
        elif u.path == "/api/cursor-key":
            kf = os.path.join(orchlib.find_state_dir(), "cursor.key")
            if self.command == "GET":
                self.send_json({"set": os.path.exists(kf),
                                "hint": "ключ хранится в .orchestration/cursor.key (в .gitignore)"})
            elif self.command == "DELETE":
                try:
                    os.unlink(kf)
                except OSError:
                    pass
                self.send_json({"ok": True, "set": False})
        elif u.path == "/api/status":
            self.send_json({"runs": self.runs_status()})
        elif u.path == "/api/logs":
            name = (q.get("name") or [""])[0]
            tail = int((q.get("tail") or ["200"])[0])
            if not SAFE_NAME.match(name):
                self.send_json({"error": "bad name"}, 400)
                return
            path = os.path.join(orchlib.find_state_dir(), name)
            if not os.path.isfile(path):
                self.send_json({"error": "not found"}, 404)
                return
            self.send_json({"name": name, "tail": self.tail_file(path, min(tail, 2000))})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        # /api/cursor-key: удаление токена (роутинг общий с GET по path)
        self.do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/params":
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            try:
                merged = orchlib._merge(orchlib.DEFAULTS, body.get("params", body))
                orchlib.save_params(merged)
                self.send_json({"ok": True, "params": merged})
            except ValueError as e:
                self.send_json({"error": str(e)}, 400)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        elif u.path == "/api/compass":
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            text = body.get("text", "")
            if not isinstance(text, str) or not text.strip():
                self.send_json({"error": "задача пустая"}, 400)
                return
            if len(text) > 65536:
                self.send_json({"error": "задача больше 64KB"}, 400)
                return
            p = orchlib.load_params()
            path = orchlib.compass_path(p)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.send_json({"ok": True, "path": path})
        elif u.path == "/api/cursor-key":
            kf = os.path.join(orchlib.find_state_dir(), "cursor.key")
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            keyv = body.get("key", "")
            if not isinstance(keyv, str) or len(keyv.strip()) < 8:
                self.send_json({"error": "ключ слишком короткий"}, 400)
                return
            os.makedirs(os.path.dirname(kf), exist_ok=True)
            with open(kf, "w", encoding="utf-8") as f:
                f.write(keyv.strip())
            self.send_json({"ok": True, "set": True})
        else:
            self.send_json({"error": "not found"}, 404)


def main():
    p = orchlib.load_params()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    host = a.host or p.get("panel", {}).get("host", "127.0.0.1")
    base = a.port or int(p.get("panel", {}).get("port", 8765))
    httpd = None
    for port in range(base, base + 5):  # 8765 занят — возьмём соседний
        try:
            httpd = HTTPServer((host, port), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        print("не нашлось свободного порта %d..%d" % (base, base + 4))
        return 1
    print("панель: http://%s:%d  (params: %s)" % (host, port, orchlib.params_file()))
    print("остановка: Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
