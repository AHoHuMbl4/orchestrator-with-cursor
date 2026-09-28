#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP-панель параметров оркестрации — редактор params.json/compass.md + просмотр
прогонов. python3.6+, только stdlib, кроссплатформенно (Linux/macOS/Windows).

Запуск:  python3 server.py [--host 127.0.0.1] [--port 8765]
По умолчанию bind 127.0.0.1; port — из .orchestration/params.json (секция panel).
params.panel.host учитывается только если loopback (127.x / localhost / ::1);
внешний bind — только через явный --host.

Панель — ТОЛЬКО редактор и наблюдатель: она не запускает задачи и не принимает
решений (решения — за оркестратором). Наружу не выставлять: доступ извне —
через ssh-туннель или tailscale.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import orchlib  # noqa: E402

PANEL_DIR = os.path.dirname(os.path.abspath(__file__))
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
OPENROUTER_KEY_RE = re.compile(r"^sk-or-v1-[A-Za-z0-9_-]{6,}$")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")
_SK_OR_BODY_RE = re.compile(r"sk-or-v1-[A-Za-z0-9_-]+")
OUTBOUND_TIMEOUT_S = 10
PROBE_FULL_TIMEOUT_S = 30
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
CURSOR_ME_URL = "https://api.cursor.com/v1/me"

# In-memory снимок сторожа compass (только наблюдение + pending-флаг).
_guard_lock = threading.Lock()
_guard_snapshot = {"overflows": [], "poll_s": 2}

# Кэш /api/health: ключ = (mtime journal, fronts, rules/manifest, HEAD)
_health_lock = threading.Lock()
_health_cache = {"key": None, "payload": None, "computed_at": 0.0}

# Статусы фронта (контракт панели; orchlib может отставать — мягкая деградация).
PANEL_FRONT_STATUSES = (
    "proposed", "active", "stalled", "cancelled", "rejected", "done",
)
DEFAULT_WARN_RUNS_PER_FRONT = 60


def _kit_head_hash():
    """Короткий/полный HEAD хэш кита для ключа кэша health; сбой → \"\"."""
    kit = getattr(orchlib, "KIT_DIR", None) or PANEL_DIR
    try:
        r = subprocess.run(
            ["git", "-C", kit, "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5,
        )
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _health_mtime_key(state):
    """Ключ кэша: mtime journal+fronts+rules/manifest + kit HEAD."""
    kit = getattr(orchlib, "KIT_DIR", None) or PANEL_DIR
    paths = [
        os.path.join(state, "journal.jsonl"),
        os.path.join(state, "fronts.json"),
        os.path.join(state, "fronts"),
        os.path.join(kit, "rules", "manifest.json"),
    ]
    mt = []
    for p in paths:
        try:
            mt.append(os.path.getmtime(p))
        except Exception:
            mt.append(0.0)
    # также max mtime внутри fronts/ (order.md и т.п.)
    fronts_dir = paths[2]
    max_inner = 0.0
    try:
        for dirpath, _dns, fns in os.walk(fronts_dir):
            for fn in fns:
                try:
                    max_inner = max(
                        max_inner,
                        os.path.getmtime(os.path.join(dirpath, fn)))
                except Exception:
                    pass
    except Exception:
        pass
    mt.append(max_inner)
    # HEAD: mask-коммиты меняют wave_no_docs без сдвига mtime journal/fronts
    return (tuple(mt), _kit_head_hash())


def _health_payload():
    """Красные чипы health (вкл. lint_failures, probes_missing, chip_silenced,
    general_resume_chain) + WARN-метка general_resume_chain_warn.

    Кэш по mtime journal+fronts и kit HEAD. Чипы из orchlib.health_red_chips;
    probes_missing/chip_silenced — канон приёмки v1 (F-ACCEPT); не фильтровать.
    Логика детекторов — только в orchlib; здесь метки/прокси.
    """
    state = orchlib.find_state_dir()
    key = _health_mtime_key(state)
    with _health_lock:
        if _health_cache["key"] == key and _health_cache["payload"] is not None:
            out = dict(_health_cache["payload"])
            out["cached"] = True
            return out
    chips = orchlib.health_red_chips(state)
    counts = {k: len(v) if isinstance(v, list) else 0 for k, v in chips.items()}
    payload = {
        "counts": counts,
        "ids": chips,
        "scan_limit": getattr(orchlib, "HEALTH_JOURNAL_SCAN_LIMIT", 5000),
        "cached": False,
    }
    with _health_lock:
        _health_cache["key"] = key
        _health_cache["payload"] = {
            "counts": counts,
            "ids": chips,
            "scan_limit": payload["scan_limit"],
        }
        _health_cache["computed_at"] = time.time()
    return payload


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


def _journal_parent(val):
    """Нормализация parent: None / отсутствует / пустая строка → None."""
    if val is None or val == "":
        return None
    return val


def _journal_read_safe(limit):
    """journal_read(limit) через getattr; нет функции → ( [], False)."""
    fn = getattr(orchlib, "journal_read", None)
    if not callable(fn):
        return [], False
    try:
        entries = fn(limit)
        if not isinstance(entries, list):
            entries = []
        return entries, True
    except Exception:
        return [], False


def _build_journal_tree(entries):
    """Склейка start+end по id → дерево корневых узлов с children."""
    nodes = {}
    order = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if eid is None or eid == "":
            continue
        kind = e.get("kind")
        parent = _journal_parent(e.get("parent"))
        if eid not in nodes:
            nodes[eid] = {
                "id": eid,
                "parent": parent,
                "role": e.get("role"),
                "engine": e.get("engine"),
                "front": e.get("front"),
                "status": None,
                "verdict": e.get("verdict"),
                "gates": e.get("gates"),
                "children": [],
            }
            order.append(eid)
        node = nodes[eid]
        if kind == "start":
            node["parent"] = parent
            if "role" in e:
                node["role"] = e.get("role")
            if "engine" in e:
                node["engine"] = e.get("engine")
            if "front" in e:
                node["front"] = e.get("front")
            if "verdict" in e:
                node["verdict"] = e.get("verdict")
            if "gates" in e:
                node["gates"] = e.get("gates")
        elif kind == "end":
            node["status"] = e.get("exit")
            if "verdict" in e:
                node["verdict"] = e.get("verdict")
            if "gates" in e:
                node["gates"] = e.get("gates")
        else:
            # неизвестный kind — мягко дописать непустые поля
            if parent is not None or "parent" in e:
                node["parent"] = parent
            for k in ("role", "engine", "front", "verdict", "gates"):
                if k in e and e.get(k) is not None:
                    node[k] = e.get(k)
            if "exit" in e:
                node["status"] = e.get("exit")

    for nid in order:
        nodes[nid]["children"] = []
    roots = []
    for nid in order:
        node = nodes[nid]
        p = _journal_parent(node.get("parent"))
        node["parent"] = p
        if p is not None and p in nodes and p != nid:
            nodes[p]["children"].append(node)
        else:
            node["parent"] = None
            roots.append(node)
    return roots


def _key_mask(key):
    """Маска ключа: «…» + последние 4 символа; иначе None."""
    if not isinstance(key, str) or len(key) < 4:
        return None
    return "…" + key[-4:]


def _clip_label(s):
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    return s[:64]


def _redact_detail(text, key=None):
    """Вырезать ключ/Bearer/похожие токены из detail; сырые upstream-тела не отдаём."""
    if text is None:
        return None
    s = str(text)
    if key and isinstance(key, str) and key:
        s = s.replace(key, "[REDACTED]")
    s = _BEARER_RE.sub("Bearer [REDACTED]", s)
    s = _SK_OR_BODY_RE.sub("[REDACTED]", s)
    if len(s) > 200:
        s = s[:200] + "…"
    return s


def _state_key_path(filename):
    state = os.path.abspath(orchlib.find_state_dir())
    return state, os.path.abspath(os.path.join(state, filename))


def _read_key_file(filename):
    """Прочитать ключ из <state>/<filename>; (key|None, path). Тело не логировать."""
    _state, path = _state_key_path(filename)
    if not os.path.exists(path):
        return None, path
    try:
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip()
    except OSError:
        return None, path
    return (key or None), path


def _write_key_file(filename, key):
    _state, path = _state_key_path(filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key.strip())
    os.chmod(path, 0o600)
    return path


def _resolve_cursor_api_key():
    """Как resolve_api_key в bin/run-cloud.py без CLI-флага: env → <state>/cursor.key."""
    key = os.environ.get("CURSOR_API_KEY")
    if key and str(key).strip():
        return str(key).strip()
    key, _path = _read_key_file("cursor.key")
    return key


def _http_bearer_get(url, key):
    """GET с Bearer; (status|None, parsed_json|None, err_note). Тело 4xx не возвращаем."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=OUTBOUND_TIMEOUT_S) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw) if raw else None
            except ValueError:
                data = None
            return code, data, None
    except urllib.error.HTTPError as e:
        # upstream 4xx может эхоить секрет — тело не читаем в detail
        try:
            e.read()
        except Exception:
            pass
        return e.code, None, "HTTP %s" % e.code
    except Exception as e:
        return None, None, "network: %s" % type(e).__name__


def _or_auth_error_class(code):
    if code in (401, 402, 403, 429):
        return str(code)
    return "network"


def _cursor_error_class(code):
    if code in (401, 403, 429):
        return str(code)
    return "network"


def _label_from_openrouter_key(data):
    if not isinstance(data, dict):
        return None
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    lab = inner.get("label") if isinstance(inner, dict) else None
    return _clip_label(_redact_detail(lab) if lab else None)


def _label_from_cursor_me(data):
    if not isinstance(data, dict):
        return None
    for k in ("name", "username", "email", "id", "apiKeyName", "keyName"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return _clip_label(_redact_detail(v))
    user = data.get("user")
    if isinstance(user, dict):
        for k in ("name", "email", "id", "username"):
            v = user.get(k)
            if isinstance(v, str) and v.strip():
                return _clip_label(_redact_detail(v))
    return None


def _probe_openrouter_full(key):
    """subprocess jev-advise.py; returncode игнорируется; timeout → network."""
    kit = getattr(orchlib, "KIT_DIR", None) or os.path.dirname(PANEL_DIR)
    advise = os.path.join(kit, "bin", "jev-advise.py")
    cmd = [
        sys.executable, advise,
        "--caller", "panel-openrouter-probe",
        "--question", "panelprobe:noul:панель проверяет работоспособность ключа",
        "--state-text", "openrouter key probe from panel",
    ]
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=PROBE_FULL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "mode": "full", "error_class": "network",
            "label": None, "mask": _key_mask(key), "detail": "timeout",
            "cost": None,
        }
    except Exception as e:
        return {
            "ok": False, "mode": "full", "error_class": "network",
            "label": None, "mask": _key_mask(key),
            "detail": _redact_detail("%s" % type(e).__name__, key),
            "cost": None,
        }
    # returncode ignore (fail-open CLI)
    out = (r.stdout or "").strip()
    data = None
    if out:
        for line in reversed(out.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
        if data is None:
            try:
                data = json.loads(out)
            except ValueError:
                data = None
    if not isinstance(data, dict):
        return {
            "ok": False, "mode": "full", "error_class": "network",
            "label": None, "mask": _key_mask(key),
            "detail": "bad jev-advise stdout", "cost": None,
        }
    ok = data.get("ok") is True
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    cost = usage.get("cost")
    if ok:
        return {
            "ok": True, "mode": "full", "error_class": None,
            "label": None, "mask": _key_mask(key), "detail": None,
            "cost": cost,
        }
    err = data.get("error") or ""
    err_s = str(err)
    if "HTTP 401" in err_s:
        ec = "401"
    elif "HTTP 402" in err_s:
        ec = "402"
    else:
        ec = "network"
    return {
        "ok": False, "mode": "full", "error_class": ec,
        "label": None, "mask": _key_mask(key),
        "detail": _redact_detail(err_s, key), "cost": cost,
    }


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

    def load_params_or_500(self):
        """load_params; при битом JSON — 500 «params.json битый», без трейсбека."""
        try:
            return orchlib.load_params()
        except ValueError:
            self.send_json({"error": "params.json битый"}, 500)
            return None

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
            p = self.load_params_or_500()
            if p is None:
                return
            self.send_json({"params": p})
        elif u.path == "/api/compass":
            p = self.load_params_or_500()
            if p is None:
                return
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
            state, kf = _state_key_path("cursor.key")
            if self.command == "GET":
                key, _p = _read_key_file("cursor.key")
                self.send_json({
                    "set": os.path.exists(kf),
                    "path": kf,
                    "state_dir": state,
                    "hint": "ключ хранится в .orchestration/cursor.key (в .gitignore)",
                    "mask": _key_mask(key) if key else None,
                })
            elif self.command == "DELETE":
                try:
                    os.unlink(kf)
                except OSError:
                    pass
                self.send_json({"ok": True, "set": False})
        elif u.path == "/api/openrouter-key":
            _state, kf = _state_key_path("openrouter.key")
            if self.command == "GET":
                key, _p = _read_key_file("openrouter.key")
                self.send_json({
                    "set": os.path.exists(kf),
                    "path": kf,
                    "mask": _key_mask(key) if key else None,
                    "hint": "ключ хранится в .orchestration/openrouter.key (в .gitignore)",
                })
            elif self.command == "DELETE":
                try:
                    os.unlink(kf)
                except OSError:
                    pass
                self.send_json({"set": False})
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
        elif u.path == "/api/health":
            self.send_json(_health_payload())
        elif u.path == "/api/compass-guard":
            with _guard_lock:
                overflows = list(_guard_snapshot.get("overflows") or [])
                poll_s = _guard_snapshot.get("poll_s", 2)
            self.send_json({"overflows": overflows, "poll_s": poll_s})
        elif u.path == "/api/fronts":
            p = self.load_params_or_500()
            if p is None:
                return
            data = orchlib.load_fronts()
            _sess_lim, front_lim = _compass_limits(p)
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
        elif u.path == "/api/journal":
            try:
                limit = int((q.get("limit") or ["300"])[0])
            except (ValueError, TypeError):
                limit = 300
            if limit < 1:
                limit = 1
            if limit > 5000:
                limit = 5000
            entries, available = _journal_read_safe(limit)
            tree = _build_journal_tree(entries) if available else []
            self.send_json({
                "entries": entries,
                "tree": tree,
                "available": available,
            })
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
            p = self.load_params_or_500()
            if p is None:
                return
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
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            keyv = body.get("key", "")
            if not isinstance(keyv, str) or len(keyv.strip()) < 8:
                self.send_json({"error": "ключ слишком короткий"}, 400)
                return
            keyv = keyv.strip()
            kf = _write_key_file("cursor.key", keyv)
            self.send_json({
                "ok": True, "set": True, "path": kf,
                "mask": _key_mask(keyv),
            })
        elif u.path == "/api/openrouter-key":
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            keyv = body.get("key", "")
            if not isinstance(keyv, str):
                self.send_json({"error": "ключ: ожидается строка"}, 400)
                return
            keyv = keyv.strip()
            if not OPENROUTER_KEY_RE.match(keyv):
                self.send_json({
                    "error": "неверный формат ключа OpenRouter "
                             "(ожидается sk-or-v1-…)",
                }, 400)
                return
            _write_key_file("openrouter.key", keyv)
            self.send_json({"set": True, "mask": _key_mask(keyv)})
        elif u.path == "/api/openrouter-key/probe":
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            full = False
            if isinstance(body, dict):
                full = body.get("full") is True or body.get("full") == 1
            key, _p = _read_key_file("openrouter.key")
            if not key:
                payload = {
                    "ok": False,
                    "mode": "full" if full else "auth",
                    "error_class": "no_key",
                    "label": None,
                    "mask": None,
                    "detail": None,
                }
                if full:
                    payload["cost"] = None
                self.send_json(payload)
                return
            if full:
                self.send_json(_probe_openrouter_full(key))
                return
            code, data, note = _http_bearer_get(OPENROUTER_KEY_URL, key)
            mask = _key_mask(key)
            if code is not None and 200 <= code < 300:
                self.send_json({
                    "ok": True, "mode": "auth", "error_class": None,
                    "label": _label_from_openrouter_key(data),
                    "mask": mask, "detail": None,
                })
                return
            ec = _or_auth_error_class(code) if code is not None else "network"
            self.send_json({
                "ok": False, "mode": "auth", "error_class": ec,
                "label": None, "mask": mask,
                "detail": _redact_detail(note, key),
            })
        elif u.path == "/api/cursor-key/probe":
            body, err = self.read_body_json()
            if err:
                self.send_json({"error": err}, 400)
                return
            key = _resolve_cursor_api_key()
            if not key:
                self.send_json({
                    "ok": False, "error_class": "no_key",
                    "label": None, "mask": None, "detail": None,
                })
                return
            code, data, note = _http_bearer_get(CURSOR_ME_URL, key)
            mask = _key_mask(key)
            if code is not None and 200 <= code < 300:
                self.send_json({
                    "ok": True, "error_class": None,
                    "label": _label_from_cursor_me(data),
                    "mask": mask, "detail": None,
                })
                return
            ec = _cursor_error_class(code) if code is not None else "network"
            self.send_json({
                "ok": False, "error_class": ec,
                "label": None, "mask": mask,
                "detail": _redact_detail(note, key),
            })
        elif u.path == "/api/template/restore":
            p = self.load_params_or_500()
            if p is None:
                return
            try:
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


def _is_loopback_host(h):
    """True для 127.x.x.x, localhost, ::1 (без учёта регистра у имени)."""
    if not isinstance(h, str) or not h:
        return False
    s = h.strip().lower()
    if s in ("localhost", "::1"):
        return True
    # IPv4 loopback: 127.0.0.0/8
    parts = s.split(".")
    if len(parts) == 4 and parts[0] == "127":
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            return False
    return False


def main():
    try:
        p = orchlib.load_params()
    except ValueError as e:
        # load_params: "params.json битый: <причина> (путь: …)"
        print("panel: %s" % e)
        sys.exit(1)
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    if a.host is not None:
        host = a.host
    else:
        cand = p.get("panel", {}).get("host", "127.0.0.1")
        if _is_loopback_host(cand):
            host = cand
        else:
            sys.stderr.write(
                "panel: params host %s не loopback — игнорирую, "
                "bind 127.0.0.1 (внешний — --host)\n" % cand
            )
            host = "127.0.0.1"
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
