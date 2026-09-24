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
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import orchlib  # noqa: E402

PANEL_DIR = os.path.dirname(os.path.abspath(__file__))
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# In-memory снимок сторожа compass (только наблюдение + pending-флаг).
_guard_lock = threading.Lock()
_guard_snapshot = {"overflows": [], "poll_s": 2}

# Статусы фронта (контракт панели; orchlib может отставать — мягкая деградация).
PANEL_FRONT_STATUSES = (
    "proposed", "active", "stalled", "cancelled", "rejected", "done",
)
DEFAULT_WARN_RUNS_PER_FRONT = 60


def _kit_version_safe():
    """Версия кита из orchlib.kit_version(); нет функции — None."""
    fn = getattr(orchlib, "kit_version", None)
    if not callable(fn):
        return None
    try:
        v = fn()
        return v if v is not None else None
    except Exception:
        return None


def _front_status_of(fid, fr):
    """status фронта: orchlib.front_status или поле из fronts.json; иначе None."""
    fn = getattr(orchlib, "front_status", None)
    if callable(fn) and fid:
        try:
            st = fn(fid)
            if st is not None:
                return st
        except Exception:
            pass
    if isinstance(fr, dict):
        st = fr.get("status")
        return st if isinstance(st, str) else None
    return None


def _front_runs_used(fid):
    """Счётчик counters/front-runs-<fid>.json → used или None."""
    if not fid:
        return None
    try:
        safe = orchlib.safe_name(fid)
    except Exception:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(fid))[:80] or "x"
    path = os.path.join(orchlib.find_state_dir(), "counters", "front-runs-%s.json" % safe)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "used" in data:
            return int(data["used"])
    except Exception:
        return None
    return None


def _runs_limit():
    """Warn-порог прогонов на фронт: params.budgets.warn_runs_per_front или 60."""
    try:
        p = orchlib.load_params()
        bud = p.get("budgets") if isinstance(p, dict) else None
        if isinstance(bud, dict) and "warn_runs_per_front" in bud:
            n = int(bud["warn_runs_per_front"])
            if n > 0:
                return n
    except Exception:
        pass
    return DEFAULT_WARN_RUNS_PER_FRONT


def _hard_runs_limit():
    """Жёсткий лимит: params.budgets.hard_runs_per_front; 0 = выключен."""
    try:
        p = orchlib.load_params()
        bud = p.get("budgets") if isinstance(p, dict) else None
        if isinstance(bud, dict) and "hard_runs_per_front" in bud:
            return max(0, int(bud["hard_runs_per_front"]))
    except Exception:
        pass
    return 0


def _observer_age_s(fid):
    """Возраст mtime fronts/<fid>/observer-heartbeat.txt в секундах; нет файла — None."""
    if not fid:
        return None
    try:
        safe = orchlib.safe_name(fid)
    except Exception:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(fid))[:80] or "x"
    path = os.path.join(orchlib.find_state_dir(), "fronts", safe, "observer-heartbeat.txt")
    if not os.path.isfile(path):
        return None
    try:
        return max(0, int(time.time() - os.path.getmtime(path)))
    except Exception:
        return None


def _persist_fronts_data(data):
    """Атомарная запись fronts.json без валидации orchlib (панель пишет status)."""
    import tempfile
    out = {
        "goal": data.get("goal", "") if isinstance(data.get("goal"), str) else "",
        "fronts": data.get("fronts") if isinstance(data.get("fronts"), list) else [],
        "notes": data.get("notes", "") if isinstance(data.get("notes"), str) else "",
    }
    pf = orchlib.fronts_path()
    d = os.path.dirname(pf)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".fronts-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, pf)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    return out


def _guard_poll_s(p):
    """Интервал сторожа: из params.compass.guard_poll_s, иначе 2; 0/битое → 2."""
    try:
        sec = (p.get("compass") or {}).get("guard_poll_s", 2)
        n = int(sec)
    except (TypeError, ValueError, AttributeError):
        return 2
    return n if n > 0 else 2


def _compass_guard_loop():
    """Daemon: опрос overflows → emit pending при наличии; снимок для API."""
    prev = None
    while True:
        poll_s = 2
        try:
            p = orchlib.load_params()
            poll_s = _guard_poll_s(p)
            overflows = orchlib.compass_overflows(p)
            key = [(o.get("path"), o.get("size"), o.get("limit")) for o in overflows]
            with _guard_lock:
                _guard_snapshot["overflows"] = list(overflows)
                _guard_snapshot["poll_s"] = poll_s
            # сравнить с prev; при появлении/наличии — pending-флаг (решений нет)
            if overflows:
                orchlib.emit_pending_compass_guard(None, overflows)
            prev = key
        except Exception as e:
            sys.stderr.write("compass guard: %s\n" % e)
        try:
            time.sleep(poll_s)
        except Exception:
            time.sleep(2)


def _compass_char_size(text):
    """Размер в символах Unicode (не байтах)."""
    if text is None:
        return 0
    if not isinstance(text, str):
        text = str(text)
    return len(text)


def _compass_limits(p=None):
    """(session_limit, front_limit) — int или None, если секция/функция не заданы.

    B1: orchlib.compass_limits(p) → (session, front) с дефолтами 8500/4000.
    Пока хелпера нет — читаем params.compass; нет секции → оба None
    (панель: «лимит не задан», POST без отказа).
    """
    if p is None:
        p = orchlib.load_params()
    fn = getattr(orchlib, "compass_limits", None)
    if callable(fn):
        try:
            lim = fn(p)
            if isinstance(lim, (tuple, list)) and len(lim) >= 2:
                return _as_limit(lim[0]), _as_limit(lim[1])
            if isinstance(lim, dict):
                return (_as_limit(lim.get("max_session_chars", lim.get("session"))),
                        _as_limit(lim.get("max_front_chars", lim.get("front"))))
        except Exception:
            pass
    sec = p.get("compass") if isinstance(p, dict) else None
    if not isinstance(sec, dict):
        return None, None
    return (_as_limit(sec.get("max_session_chars")),
            _as_limit(sec.get("max_front_chars")))


def _as_limit(v):
    """Положительное int-лимит или None."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _compass_over_limit_msg(size, limit, kind="сессионный"):
    return ("compass превышен: %d символов при лимите %d (%s). "
            "Файл не записан. Ужмите: историю — в артефакты, не в compass."
            % (size, limit, kind))


def _compass_file_size(path):
    """Число символов в файле; 0 если нет/не читается."""
    if not path or not os.path.isfile(path):
        return 0
    fn = getattr(orchlib, "compass_char_size", None)
    if callable(fn):
        try:
            n = fn(path)
            return int(n) if n is not None else 0
        except Exception:
            pass
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _compass_char_size(f.read())
    except Exception:
        return 0


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
                    with open(pid_path, "r", encoding="utf-8", errors="replace") as f:
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
            sid = (q.get("id") or [""])[0]
            import re as _re
            if sid and not _re.match(r"^[A-Za-z0-9._-]{1,80}$", sid):
                self.send_json({"error": "bad session id"}, 400)
                return
            target = orchlib.session_compass_path(p, sid) if sid else orchlib.compass_path(p)
            try:
                with open(target, "r", encoding="utf-8") as f:
                    text = f.read()[:200000]
            except Exception:
                text = ""
            sess_lim, _front_lim = _compass_limits(p)
            size = _compass_char_size(text)
            self.send_json({
                "path": target, "text": text, "session": sid,
                "compass_size": size,
                "compass_limit": sess_lim,  # None → UI: «лимит не задан»
            })
        elif u.path == "/api/discovered":
            dj = orchlib.state_path("discovered.json")
            if os.path.exists(dj):
                with open(dj, "r", encoding="utf-8") as f:
                    self.send_json(json.load(f))
            else:
                self.send_json({"error": "discovered.json нет — запусти bin/discover.py"}, 404)
        elif u.path == "/api/cursor-key":
            state = os.path.abspath(orchlib.find_state_dir())
            kf = os.path.abspath(os.path.join(state, "cursor.key"))
            if self.command == "GET":
                self.send_json({"set": os.path.exists(kf),
                                "path": kf,
                                "state_dir": state,
                                "hint": "ключ хранится в .orchestration/cursor.key (в .gitignore)"})
            elif self.command == "DELETE":
                try:
                    os.unlink(kf)
                except OSError:
                    pass
                self.send_json({"ok": True, "set": False})
        elif u.path == "/api/sessions":
            if self.command == "DELETE":
                # сброс override → наследовать общий тумблер
                sid = (q.get("id") or [""])[0]
                import re as _re2
                if not _re2.match(r"^[A-Za-z0-9._-]{1,80}$", sid):
                    self.send_json({"error": "bad id"}, 400)
                    return
                try:
                    os.unlink(os.path.join(orchlib.session_dir(sid), "enabled.json"))
                except OSError:
                    pass
                self.send_json({"ok": True, "id": sid, "override": None})
                return
            if self.command == "POST":
                body, err = self.read_body_json()
                if err:
                    self.send_json({"error": err}, 400)
                    return
                sid = body.get("id", "")
                import re as _re
                if not _re.match(r"^[A-Za-z0-9._-]{1,80}$", sid):
                    self.send_json({"error": "bad session id"}, 400)
                    return
                sd = orchlib.session_dir(sid)
                with open(os.path.join(sd, "enabled.json"), "w", encoding="utf-8") as f:
                    json.dump({"enabled": bool(body.get("enabled"))}, f, ensure_ascii=False)
                self.send_json({"ok": True, "id": sid, "enabled": bool(body.get("enabled"))})
            else:
                import datetime as _dt
                sessions = orchlib.list_sessions()
                visible = []
                for s in sessions:
                    sd = orchlib.session_dir(s["id"])
                    has_runs = os.path.isdir(os.path.join(sd, "runs")) and os.listdir(os.path.join(sd, "runs"))
                    import time as _time
                    recent = s.get("last_seen", 0) and (_time.time() - s["last_seen"]) < 3600
                    if s["id"] == "install-check":
                        continue  # служебная сессия self-check
                    if not s.get("has_compass") and s.get("override") is None and not has_runs and not recent:
                        continue  # старая сессия без контента — скрыть
                    s["last_seen_h"] = _dt.datetime.fromtimestamp(s["last_seen"]).strftime("%H:%M:%S") if s["last_seen"] else "—"
                    visible.append(s)
                self.send_json({"sessions": visible})
        elif u.path == "/api/status":
            self.send_json({"runs": self.runs_status()})
        elif u.path == "/api/compass-guard":
            with _guard_lock:
                overflows = list(_guard_snapshot.get("overflows") or [])
                poll_s = _guard_snapshot.get("poll_s", 2)
            self.send_json({"overflows": overflows, "poll_s": poll_s})
        elif u.path == "/api/fronts":
            data = orchlib.load_fronts()
            _sess_lim, front_lim = _compass_limits(orchlib.load_params())
            runs_lim = _runs_limit()
            hard_lim = _hard_runs_limit()
            fronts_out = []
            for fr in data.get("fronts") or []:
                if not isinstance(fr, dict):
                    continue
                item = dict(fr)
                fid = item.get("id", "")
                cp = orchlib.front_compass_path(fid) if fid else ""
                item["compass"] = item.get("compass") or cp
                item["compass_exists"] = bool(cp and os.path.isfile(cp))
                item["compass_path"] = cp
                item["compass_size"] = _compass_file_size(cp) if item["compass_exists"] else 0
                item["compass_limit"] = front_lim  # None → UI: «лимит не задан»
                item["status"] = _front_status_of(fid, fr)
                item["runs_used"] = _front_runs_used(fid)
                item["runs_limit"] = runs_lim
                item["hard_runs_per_front"] = hard_lim
                item["observer_age_s"] = _observer_age_s(fid)
                fronts_out.append(item)
            try:
                waves = orchlib.front_waves({"goal": data.get("goal", ""),
                                             "fronts": data.get("fronts") or [],
                                             "notes": data.get("notes", "")})
            except ValueError:
                waves = []
            self.send_json({
                "goal": data.get("goal", ""),
                "fronts": fronts_out,
                "notes": data.get("notes", ""),
                "waves": waves,
                "compass_limit": front_lim,
                "kit_version": _kit_version_safe(),
                "runs_limit": runs_lim,
                "hard_runs_per_front": hard_lim,
            })
        elif u.path == "/api/logs":
            name = (q.get("name") or [""])[0]
            try:
                tail = int((q.get("tail") or ["200"])[0])
            except (ValueError, TypeError):
                tail = 200
            if not SAFE_NAME.match(name) or not name.endswith(".log"):
                self.send_json({"error": "bad name (только *.log)"}, 400)
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

    def do_PUT(self):
        self.do_POST()

    def _save_fronts_request(self):
        body, err = self.read_body_json()
        if err:
            self.send_json({"error": err}, 400)
            return
        raw = body
        if isinstance(body, dict) and "fronts" in body and (
                isinstance(body.get("fronts"), list) or
                "goal" in body or "notes" in body):
            raw = {
                "goal": body.get("goal", ""),
                "fronts": body.get("fronts") if isinstance(body.get("fronts"), list) else [],
                "notes": body.get("notes", ""),
            }
        elif isinstance(body, dict) and isinstance(body.get("data"), dict):
            raw = body["data"]
        if not isinstance(raw, dict):
            self.send_json({"error": "ожидается объект fronts.json"}, 400)
            return
        try:
            orchlib.save_fronts(raw)
            data = orchlib.load_fronts()
            waves = orchlib.front_waves(data)
            self.send_json({"ok": True, "goal": data["goal"], "fronts": data["fronts"],
                            "notes": data["notes"], "waves": waves})
        except ValueError as e:
            msg = e.args[0] if e.args else str(e)
            if isinstance(msg, list):
                text = "; ".join(str(x) for x in msg)
            else:
                text = str(e)
            self.send_json({"error": text}, 400)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/fronts":
            self._save_fronts_request()
            return
        if u.path == "/api/fronts/status":
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            if not isinstance(body, dict):
                self.send_json({"error": "ожидается объект {id, status}"}, 400)
                return
            fid = body.get("id")
            status = body.get("status")
            if not isinstance(fid, str) or not fid.strip():
                self.send_json({"error": "id: ожидается непустая строка"}, 400)
                return
            if status not in PANEL_FRONT_STATUSES:
                self.send_json({
                    "error": "status: ожидается %s" % "|".join(PANEL_FRONT_STATUSES),
                }, 400)
                return
            data = orchlib.load_fronts()
            fronts = data.get("fronts") if isinstance(data.get("fronts"), list) else []
            found = None
            for fr in fronts:
                if isinstance(fr, dict) and fr.get("id") == fid:
                    found = fr
                    break
            if found is None:
                self.send_json({"error": "фронт не найден: %s" % fid}, 404)
                return
            found["status"] = status
            try:
                # save_fronts может отвергнуть новые статусы, пока orchlib отстаёт —
                # пишем напрямую (владелец панели / ручная отмена).
                try:
                    orchlib.save_fronts(data)
                except ValueError:
                    _persist_fronts_data(data)
                self.send_json({
                    "ok": True,
                    "id": fid,
                    "status": status,
                    "kit_version": _kit_version_safe(),
                })
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return
        if u.path == "/api/params":
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            raw = body.get("params") if isinstance(body, dict) else None
            if raw is None:
                raw = body if isinstance(body, dict) and "params" not in body else raw
            if not isinstance(raw, dict):
                self.send_json({"error": "params: ожидается объект"}, 400)
                return
            try:
                merged = orchlib._merge(orchlib.DEFAULTS, raw)
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
            sid = body.get("id", "") or ""
            if not isinstance(text, str) or not text.strip():
                self.send_json({"error": "задача пустая"}, 400)
                return
            if len(text) > 65536:
                self.send_json({"error": "задача больше 64KB"}, 400)
                return
            import re as _re
            if sid and not _re.match(r"^[A-Za-z0-9._-]{1,80}$", sid):
                self.send_json({"error": "bad session id"}, 400)
                return
            if not sid and body.get("confirm_template") is not True:
                self.send_json({"error": "глобальный compass — шаблон; подтвердите запись"}, 400)
                return
            p = orchlib.load_params()
            sess_lim, _front_lim = _compass_limits(p)
            size = _compass_char_size(text)
            # Отказ только если лимит задан в params/orchlib; иначе — как раньше.
            if sess_lim is not None and size > sess_lim:
                self.send_json({
                    "error": _compass_over_limit_msg(size, sess_lim, "сессионный"),
                    "compass_size": size,
                    "compass_limit": sess_lim,
                }, 400)
                return
            if sid:
                path = os.path.join(orchlib.session_dir(sid), "compass.md")
            else:
                path = orchlib.compass_path(p)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.send_json({
                "ok": True, "path": path,
                "compass_size": size,
                "compass_limit": sess_lim,
            })
        elif u.path == "/api/sessions":
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            sid = body.get("id") or ""
            if not isinstance(sid, str):
                self.send_json({"error": "id: ожидается строка"}, 400)
                return
            import re as _re
            if not _re.match(r"^[A-Za-z0-9._-]{1,80}$", sid):
                self.send_json({"error": "bad session id"}, 400)
                return
            sd = orchlib.session_dir(sid)
            with open(os.path.join(sd, "enabled.json"), "w", encoding="utf-8") as f:
                json.dump({"enabled": bool(body.get("enabled"))}, f, ensure_ascii=False)
            self.send_json({"ok": True, "id": sid, "enabled": bool(body.get("enabled"))})
        elif u.path == "/api/cursor-key":
            state = os.path.abspath(orchlib.find_state_dir())
            kf = os.path.abspath(os.path.join(state, "cursor.key"))
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
            self.send_json({"ok": True, "set": True, "path": kf})
        elif u.path == "/api/template/restore":
            try:
                p = orchlib.load_params()
                dst = orchlib.compass_path(p)
                src = os.path.join(orchlib.KIT_DIR, "compass.md")
                if not os.path.isfile(src):
                    self.send_json({"error": "нет kit-шаблона: %s" % src}, 500)
                    return
                with open(src, "r", encoding="utf-8") as f:
                    text = f.read()
                bak = dst + ".bak-restore"
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.isfile(dst):
                    with open(dst, "rb") as f:
                        old = f.read()
                    with open(bak, "wb") as f:
                        f.write(old)
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(text)
                self.send_json({"ok": True, "path": dst, "text": text})
            except Exception as e:
                self.send_json({"error": "не удалось восстановить шаблон: %s" % e}, 500)
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
    note = orchlib.state_dir_note()
    if note:
        print(note)
    t = threading.Thread(target=_compass_guard_loop, name="compass-guard", daemon=True)
    t.start()
    print("остановка: Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
