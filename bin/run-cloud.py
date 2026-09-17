#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Облачный исполнитель: Cursor Cloud Agents API (https://api.cursor.com).

Сценарий «нулевая установка»: на компьютере НЕ нужен ни cursor-agent, ни что-либо
ещё — задачи уходят в облачные VM Cursor, результат (текст + ветка/PR + артефакты)
читается программно. Нужен только API-ключ (Cursor Dashboard → API Keys), paid-план.

Эндпоинты (public beta — сверять схему с
https://cursor.com/docs/cloud-agent/api/endpoints):
  POST /v1/agents                          — создать агента + первый run
  POST /v1/agents/{agentId}/runs           — follow-up в существующего агента
  GET  /v1/agents/{agentId}/runs/{runId}   — статус/результат
  GET  /v1/agents/{agentId}/runs/{runId}/stream — SSE-прогресс
  GET  /v1/agents/{agentId}/artifacts      — артефакты (presigned download)

Тело create/follow-up: "prompt" — объект {"text": "<строка>"}, не голая строка
(см. Request Body → prompt.text в доках выше).

Подкоманды:
  run       — создать агента (или follow-up) с промтом из файла
  status    — статус/результат run (опция --wait: поллить до готовности)
  artifacts — список артефактов агента

Общие флаги (--id, --api-key) работают ДО и ПОСЛЕ субкоманды:
  run-cloud.py --id w1 run --prompt-file P.md
  run-cloud.py run --id w1 --prompt-file P.md

Ключ (по приоритету): 1) --api-key; 2) env CURSOR_API_KEY; 3) файл
<state>/cursor.key (state — .orchestration, ищется от cwd вверх / ORCHESTRATION_DIR).
Всё пишется в <state>/cloud-<id>.log.
Схема beta: первый живой прогон калибрует парсинг id (ответ логируется целиком).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

BASE = "https://api.cursor.com"


def _add_common_args(parser):
    """Общие флаги на main и на субпарсерах (default=SUPPRESS → оба порядка)."""
    parser.add_argument("--api-key", default=argparse.SUPPRESS)
    parser.add_argument("--id", default=argparse.SUPPRESS)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    _add_common_args(ap)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run", help="создать агента / follow-up")
    _add_common_args(p)
    p.add_argument("--prompt-file", default=None)
    p.add_argument("--agent-id", default=None, help="follow-up в существующего агента")
    p.add_argument("--body-file", default=None,
                   help="json с доп. полями запроса (repo/config — по докам beta)")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--poll", type=int, default=15)

    p = sub.add_parser("status", help="статус/результат run")
    _add_common_args(p)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--wait", action="store_true")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--poll", type=int, default=15)

    p = sub.add_parser("artifacts", help="список артефактов агента")
    _add_common_args(p)
    p.add_argument("--agent-id", required=True)

    return ap


def normalize_args(a):
    """Дозаполнить общие флаги, если пришли только с одной стороны парсера."""
    if not hasattr(a, "api_key"):
        a.api_key = None
    if not hasattr(a, "id"):
        a.id = "C1"
    return a


def api_key(a):
    key = a.api_key or os.environ.get("CURSOR_API_KEY")
    kf = os.path.abspath(os.path.join(orchlib.find_state_dir(), "cursor.key"))
    if not key:
        if os.path.exists(kf):
            with open(kf, "r", encoding="utf-8") as f:
                key = f.read().strip()
    if not key:
        sys.stderr.write("нужен --api-key, env CURSOR_API_KEY или файл %s "
                         "(вносится в панели; state-каталог ищется от cwd вверх)\n" % kf)
        sys.exit(2)
    return key


def http_error_message(body):
    """Краткое сообщение из тела 4xx/5xx (строка или JSON)."""
    body = body or ""
    try:
        j = json.loads(body)
    except ValueError:
        return body.strip() or "(пустое тело)"
    if isinstance(j, dict):
        for k in ("message", "error", "detail", "msg"):
            v = j.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, dict):
                return json.dumps(v, ensure_ascii=False)
        return json.dumps(j, ensure_ascii=False)
    return str(j)


def report_http_error(out):
    """Печать краткой диагностики HTTP-ошибки в stderr. True если это ошибка."""
    if not isinstance(out, dict) or "_http_error" not in out:
        return False
    code = out["_http_error"]
    msg = http_error_message(out.get("_body", ""))
    sys.stderr.write("HTTP %s: %s\n" % (code, msg))
    return True


def call(method, path, key, body=None, stream=False):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        if stream:
            return resp  # итерируем SSE-строки
        raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            return {"_raw": raw}
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", "replace")}


def logf(state, a):
    return open(os.path.join(state, "cloud-%s.log" % a.id), "a", encoding="utf-8")


def find_ids(obj):
    """Beta-схема: вытаскиваем вероятные id агента/рана, не полагаясь на имена."""
    ids = {}
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                lk = k.lower()
                if isinstance(v, str) and ("agent" in lk and lk.endswith("id")):
                    ids.setdefault("agent_id", v)
                if isinstance(v, str) and ("run" in lk and lk.endswith("id")):
                    ids.setdefault("run_id", v)
                if isinstance(v, str) and lk in ("id",) and "agent_id" not in ids:
                    ids.setdefault("first_id", v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return ids


def prompt_body(text):
    """Формат API: prompt — объект с полем text (не строка)."""
    return {"text": text}


def cmd_run(a):
    key = api_key(a)
    state = orchlib.find_state_dir()
    os.makedirs(state, exist_ok=True)
    prompt_file = a.prompt_file or os.path.join(state, "prompt-%s.md" % a.id)
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()
    if not prompt.strip():
        sys.stderr.write("промт пуст: %s\n" % prompt_file)
        return 2

    extra = {}
    if a.body_file:
        with open(a.body_file, "r", encoding="utf-8") as f:
            extra = json.load(f)

    lf = logf(state, a)
    lf.write("=== %s run\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    body = dict(extra)
    body["prompt"] = prompt_body(prompt)
    if a.agent_id:
        out = call("POST", "/v1/agents/%s/runs" % a.agent_id, key, body)
        lf.write("follow-up %s:\n%s\n" % (a.agent_id, json.dumps(out, ensure_ascii=False, indent=2)))
    else:
        out = call("POST", "/v1/agents", key, body)
        lf.write("create:\n%s\n" % json.dumps(out, ensure_ascii=False, indent=2))
    ids = find_ids(out)
    lf.write("ids: %s\n" % ids)
    lf.close()

    if report_http_error(out):
        print(json.dumps({"response": out, "ids": ids}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"response": out, "ids": ids}, ensure_ascii=False, indent=2))

    if a.wait:
        agent = a.agent_id or ids.get("agent_id")
        run = ids.get("run_id") or ids.get("first_id")
        if agent and run:
            return wait_and_report(agent, run, key, state, a)
        sys.stderr.write("--wait: id не найдены в ответе, поллинг пропущен (см. лог)\n")
        return 0
    return 0


def wait_and_report(agent, run, key, state, a):
    deadline = time.time() + (a.timeout or 1800)
    status = {}
    lf = logf(state, a)
    while time.time() < deadline:
        status = call("GET", "/v1/agents/%s/runs/%s" % (agent, run), key)
        lf.write("poll: %s\n" % json.dumps(status, ensure_ascii=False))
        if report_http_error(status):
            lf.close()
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 1
        raw = json.dumps(status)
        if '"done"' in raw or '"completed"' in raw or '"failed"' in raw or '"error"' in raw:
            break
        time.sleep(a.poll or 15)
    lf.write("final: %s\n" % json.dumps(status, ensure_ascii=False, indent=2))
    lf.close()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def cmd_status(a):
    key = api_key(a)
    state = orchlib.find_state_dir()
    if a.wait:
        return wait_and_report(a.agent_id, a.run_id, key, state, a)
    out = call("GET", "/v1/agents/%s/runs/%s" % (a.agent_id, a.run_id), key)
    if report_http_error(out):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with logf(state, a) as lf:
        lf.write("status %s/%s: %s\n" % (a.agent_id, a.run_id,
                                          json.dumps(out, ensure_ascii=False)))
    return 0


def cmd_artifacts(a):
    key = api_key(a)
    out = call("GET", "/v1/agents/%s/artifacts" % a.agent_id, key)
    if report_http_error(out):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with logf(orchlib.find_state_dir(), a) as lf:
        lf.write("artifacts %s: %s\n" % (a.agent_id, json.dumps(out, ensure_ascii=False)))
    return 0


def main():
    orchlib.utf8_stdio()
    note = orchlib.state_dir_note()
    if note:
        sys.stderr.write(note + "\n")
    ap = build_parser()
    a = normalize_args(ap.parse_args())
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "artifacts":
        return cmd_artifacts(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
