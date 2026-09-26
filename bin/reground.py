#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-ground движок: периодически возвращает агента к заданию и параметрам.

ГЛАВНЫЙ ТРИГГЕР — ВРЕМЯ (every_min), запасной — счётчик вызовов (every_n_calls).
Компакшн — лишь дополнительная точка (SessionStart после compact), не основная:
агент дрейфит задолго до компакшна, поэтому поправка идёт по таймеру.

Подкоманды:
  session-start  — вклейка params+compass в начало сессии (и после компакшна)
  post-tool      — счётчик/таймер на каждый tool call (Claude/Codex/Kimi)
  pre-tool       — PreToolUse: deny Write/Edit в compass (exit 2) — Kimi и др.
  subagent-start — SubagentStart → journal kind=start (engine-subagent)
  subagent-stop  — SubagentStop → journal kind=end (engine-subagent)
  heartbeat      — wall-clock для Kimi SessionHeartbeat (60-секундный тик)
  stop           — зарезервировано, молчит (не мешает завершению turn)

Формат вывода: --format json (Claude/Codex: hookSpecificOutput.additionalContext)
или text (Kimi: stdout дописывается в контекст при exit 0).

Кроссплатформенно (Windows: python/py -3), python3.6+, только stdlib.
Выход обычно 0 (ошибка чтения — тоже 0, хук не должен ронять сессию;
ошибка в stderr). Исключение: pre-tool → exit 2 при блоке compass-пути.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

NUDGE_TEXT = (
    "СВЕРКА КУРСА: ты — оркестратор. Твои правила: исполнение через "
    "исполнителей (не сам), промты из библиотеки ролей, приёмка замером, "
    "критики волнами. Прошло {minutes} мин, {calls} вызовов.\n"
    "{summary}\n"
    "Compass: {compass}\n"
    "Перечитай compass сейчас и подтверди одним абзацем: (1) цель; "
    "(2) какая строка TODO сейчас в работе; (3) ведёт ли следующий шаг "
    "к цели. Если делаешь работу исполнителя сам — остановись и делегируй. "
    "При расхождении — вернись к последней закрытой строке TODO."
)

_MAP_MD = os.path.join(
    orchlib.KIT_DIR, "skills", "orchestration", "references", "MAP.md"
)
_MAP_ORIENT = (
    "Карта системы (что где: инструменты/state/роли/доки): %s — сверяйся с ней "
    "ДО любого поиска файлов; механика скилла не опциональна."
) % _MAP_MD

SESSION_TEXT = (
    "Контекст пачки (вклеен автоматически).\n"
    "{summary}\n\n"
    "Задание (compass, полный текст):\n"
    "{compass_text}\n\n"
    "Правило курса: сверка с compass идёт периодически по таймеру; "
    "перед каждой волной/пачкой — перечитай compass и params заново.\n"
    + _MAP_ORIENT
)


def emit(fmt, event, text):
    """json — для Claude/Codex hooks; text — для Kimi (stdout append)."""
    if fmt == "text":
        sys.stdout.write(text)
        return
    payload = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:80] or "default"


def counter_file(session_id):
    d = os.path.join(orchlib.find_state_dir(), "counters")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, safe_name(session_id) + ".json")


def read_stdin_json():
    if sys.stdin.isatty():
        raw = ""
    elif hasattr(sys.stdin, "buffer"):
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    else:
        raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def enabled(p, sid=None):
    """Тумблер: per-session override > params.orchestration.enabled."""
    return orchlib.session_effective_enabled(p, sid)


def session_id_of(ev):
    return ev.get("session_id") or ev.get("sessionId") or "default"


def compass_of(p, sid):
    return orchlib.session_compass_path(p, sid)


KIT_UPDATE_MSG = (
    "🔔 СИСТЕМА ОБНОВЛЕНА (было {old} → стало {new}). "
    "Перечитай С ДИСКА SKILL.md и MAP.md; всё в истории чата, им противоречащее, "
    "ОТМЕНЕНО. Работай по новой доктрине."
)
KIT_LIVE_FRONTS_MSG = (
    "⚠️ Живые фронты: обновление применять на ГРАНИЦЕ волны "
    "(после приёмки и OK наблюдателя), не посреди"
)

STATE_WITHOUT_PROJECT_MSG = (
    "⚠️ STATE БЕЗ ПРОЕКТА: здесь есть граф фронтов, но нет PROJECT.md. "
    "Если это другая работа — НЕ продолжай чужой граф: открой отдельную "
    "папку проекта верхнего уровня (свой .orchestration) или создай "
    "PROJECT.md, если ты действительно продолжаешь этот проект."
)

STATE_PARENT_MSG = (
    "⚠️ STATE РОДИТЕЛЯ: работаешь в чужом дереве (state: %s); "
    "продолжать чужой граф только осознанно"
)


def foreign_state_warning():
    """Предупреждение о чужом/беспроектном state при наличии фронтов.

    Молчание только при своём state + PROJECT.md.
    fronts + state_is_foreign → STATE РОДИТЕЛЯ (даже если PROJECT.md есть).
    fronts + нет PROJECT.md → STATE БЕЗ ПРОЕКТА.
    Тихие ошибки → пустая строка.
    """
    try:
        state = orchlib.find_state_dir()
        fronts_path = os.path.join(state, "fronts.json")
        with open(fronts_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        fronts = data.get("fronts") if isinstance(data, dict) else None
        if not (isinstance(fronts, list) and len(fronts) > 0):
            return ""
        foreign = orchlib.state_is_foreign()
        if foreign:
            return STATE_PARENT_MSG % foreign
        project_md = os.path.join(os.path.dirname(state), "PROJECT.md")
        if not os.path.exists(project_md):
            return STATE_WITHOUT_PROJECT_MSG
    except Exception:
        pass
    return ""


def _orch_soft(name, *args):
    """Вызов orchlib.<name>; нет функции / ошибка → (None, False), не падать."""
    fn = getattr(orchlib, name, None)
    if not callable(fn):
        return None, False
    try:
        return fn(*args), True
    except Exception as e:
        sys.stderr.write("orchlib.%s failed: %s\n" % (name, e))
        return None, False


def kit_paste_for_session(sid):
    """Версия кита для вклейки + префикс громкого обновления (или пусто).

    Деградация: нет kit_version → ('?', ''); нет last_seen/mark → версия без
    громкого блока (проверку смены пропускаем).
    """
    ver, vok = _orch_soft("kit_version")
    if not vok or ver is None or ver == "":
        return "?", ""
    ver = str(ver)
    last, lok = _orch_soft("last_seen_kit_version", sid)
    if not lok:
        return ver, ""
    if last == ver:
        return ver, ""
    old = last if last is not None else "нет"
    parts = [KIT_UPDATE_MSG.format(old=old, new=ver)]
    fronts, fok = _orch_soft("active_fronts")
    if fok and fronts:
        parts.append(KIT_LIVE_FRONTS_MSG)
    _orch_soft("mark_kit_version", sid, ver)
    return ver, "\n".join(parts) + "\n"


def cmd_session_start(engine, fmt):
    """reground.compact_reground=false — не вклеивать (тихо выйти)."""
    ev = read_stdin_json()
    sid = session_id_of(ev)
    orchlib.touch_session(sid)
    p = orchlib.load_params()
    seeded = orchlib.seed_session_compass(p, sid)
    if not enabled(p, sid):
        return
    if p.get("reground", {}).get("compact_reground", True) == False:
        return
    cpath = compass_of(p, sid)
    limit = orchlib.compass_session_limit(p)
    try:
        with open(cpath, "r", encoding="utf-8") as f:
            full = f.read()
        n = len(full)
        if n > limit:
            ctext = (full[:limit] +
                     "\n⚠️ ВКЛЕЙКА ОБРЕЗАНА: показаны первые %d из %d символов; "
                     "хвост НЕ виден — ужми файл." % (limit, n))
        else:
            ctext = full
    except Exception:
        if seeded is False and not os.path.exists(cpath):
            ctext = ("(compass этой сессии ещё не создан — СОЗДАЙ его по пути %s "
                     "при первой задаче: цель, критерий, TODO-чеклист, границы)") % cpath
        else:
            ctext = ("(compass этой сессии создан из общего шаблона: %s — заполни Цель, "
                     "критерий приёмки и TODO-чеклист под текущую задачу; это твоя "
                     "личная копия, общий шаблон не трогай)") % cpath
    text = SESSION_TEXT.format(
        summary=orchlib.params_summary(p),
        compass_text=ctext,
    )[:9500]
    text = ("Сессия: %s. Compass этой сессии: %s\n\n" % (sid, cpath) + text)[:9800]
    emit(fmt, "SessionStart", text)


def _tool_write_path(ev):
    """Целевой путь записи из Post/PreToolUse: tool_name + tool_input.file_path.

    engine-agnostic (Claude/Codex/Kimi): tool_name|tool|toolName;
    tool_input.file_path|path.
    """
    tool = ev.get("tool_name") or ev.get("toolName") or ev.get("tool") or ""
    write_tools = ("Write", "Edit", "MultiEdit", "NotebookEdit")
    if tool not in write_tools:
        return None
    ti = ev.get("tool_input") or ev.get("toolInput") or {}
    if not isinstance(ti, dict):
        return None
    path = ti.get("file_path") or ti.get("filePath") or ti.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


def _tool_input_path(ev):
    """Путь из tool_input (file_path/path), без фильтра по имени инструмента."""
    ti = ev.get("tool_input") or ev.get("toolInput") or {}
    if not isinstance(ti, dict):
        return None
    path = ti.get("file_path") or ti.get("filePath") or ti.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


def cmd_pre_tool(engine, fmt):
    """PreToolUse: блок прямого Write/Edit в compass-путь состояния (exit 2).

    ОГРАНИЧЕНИЕ: PreToolUse/PostToolUse гарантированно действуют в сессии,
    где хуки зарегистрированы (главная/командующий); для субагентов движка —
    зависит от того, стреляют ли события на их вызовах (проверить на живой
    машине: запустить субагента с Write в compass и посмотреть, заблокирует
    ли). SubagentStart/Stop дают видимость генералов независимо.
    """
    ev = read_stdin_json()
    path = _tool_input_path(ev)
    if path and orchlib.is_compass_path(path):
        msg = (
            "⛔ Компас пишется ТОЛЬКО через воронку: python3 %s/bin/write-compass.py "
            "--path <путь> --text-file <ф>. Прямой Write/Edit запрещён."
        ) % orchlib.KIT_DIR
        sys.stderr.write(msg + "\n")
        return 2
    return 0


def _subagent_name(ev):
    """Имя субагента из события SubagentStart/Stop (sanitize снаружи)."""
    for k in ("agent_name", "agentName", "subagent_name", "subagentName",
              "agent_type", "agentType", "subagent_type", "subagentType",
              "name", "matcher"):
        v = ev.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    agent = ev.get("agent")
    if isinstance(agent, dict):
        for k in ("name", "type", "agent_type", "agent_name"):
            v = agent.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return "unknown"


def cmd_subagent_lifecycle(kind):
    """kind: start|end — запись в journal; ошибки тихие."""
    try:
        ev = read_stdin_json()
        name = safe_name(_subagent_name(ev))
        orchlib.journal_append({
            "kind": kind,
            "id": "subagent:" + name,
            "engine": "engine-subagent",
            "parent": session_id_of(ev),
            "ts": time.time(),
            "extra": {"event": ev},
        })
    except Exception:
        pass
    return 0


def cmd_post_tool(engine, fmt):
    # engine не влияет на логику (только fmt из main для kimi→text); без
    # claude-специфики — счётчики нуджа + compass-гард записи.
    ev = read_stdin_json()
    session_id = session_id_of(ev)
    orchlib.touch_session(session_id)
    p = orchlib.load_params()
    if not enabled(p, session_id):
        return
    rg = p.get("reground", {})
    every_min = int(rg.get("every_min", 7))
    every_calls = int(rg.get("every_n_calls", 40))

    cf = counter_file(session_id)
    now = time.time()
    data = {"start_ts": now, "calls": 0, "calls_at_nudge": 0, "last_nudge_ts": now}
    try:
        with open(cf, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
    except Exception:
        pass

    data["calls"] = int(data.get("calls", 0)) + 1
    elapsed = now - float(data.get("last_nudge_ts", now))
    since_nudge_calls = data["calls"] - int(data.get("calls_at_nudge", 0))

    triggered = None
    if elapsed >= every_min * 60:
        triggered = "%d мин" % int(elapsed // 60)
    elif since_nudge_calls >= every_calls:
        triggered = "%d вызовов" % since_nudge_calls

    parts = []
    if triggered:
        parts.append(NUDGE_TEXT.format(
            minutes=int(elapsed // 60), calls=since_nudge_calls,
            summary=orchlib.params_summary(p),
            compass=compass_of(p, session_id),
        )[:9000])
        data["last_nudge_ts"] = now
        data["calls_at_nudge"] = data["calls"]

    try:
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        sys.stderr.write("counter write failed: %s\n" % e)

    # гард: только если инструмент писал в compass-путь и файл превысил лимит
    wpath = _tool_write_path(ev)
    if wpath and orchlib.is_compass_path(wpath):
        size = orchlib.compass_char_size(wpath)
        if size is not None:
            limit = orchlib.compass_limit_for_path(p, wpath)
            if size > limit:
                parts.append(orchlib.format_compass_overflows(
                    [{"path": os.path.normpath(os.path.abspath(wpath)),
                      "size": size, "limit": limit}]))

    if parts:
        emit(fmt, "PostToolUse", "\n".join(parts)[:9000])


def cmd_heartbeat(engine, fmt):
    """Kimi SessionHeartbeat: на входе uptime_ms; таймер — по аптайму (монотонно)."""
    ev = read_stdin_json()
    sid = session_id_of(ev)
    orchlib.touch_session(sid)
    p = orchlib.load_params()
    if not enabled(p, sid):
        return
    # SessionHeartbeat — observation-only (stdout НЕ в контекст модели):
    # при overflow — pending-флаг гарда; доставка — в UserPromptSubmit.
    overflows = orchlib.compass_overflows(p)
    if overflows:
        flag_g = os.path.join(orchlib.session_dir(sid), "pending_compass_guard.json")
        try:
            with open(flag_g, "w", encoding="utf-8") as f:
                json.dump({"overflows": overflows}, f, ensure_ascii=False)
            sys.stderr.write("compass guard pending: %d file(s)\n" % len(overflows))
        except Exception as e:
            sys.stderr.write("compass guard flag failed: %s\n" % e)
    every_min = int(p.get("reground", {}).get("every_min", 7))
    uptime_ms = ev.get("uptime_ms")
    if not isinstance(uptime_ms, (int, float)):
        return
    cf = os.path.join(orchlib.find_state_dir(), "counters", "heartbeat-%s.json" % safe_name(sid))
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    last = None
    try:
        with open(cf, "r", encoding="utf-8") as f:
            last = json.load(f).get("uptime_ms")
    except Exception:
        pass
    # heartbeat только решает «пора» и ставит флажок nudge; доставит следующий
    # UserPromptSubmit (у него append в контекст документирован).
    if last is None:
        last = uptime_ms  # первый тик: инициализация без напоминания
    elif (uptime_ms - last) >= every_min * 60 * 1000:
        flag = os.path.join(orchlib.session_dir(sid), "pending_nudge.json")
        try:
            with open(flag, "w", encoding="utf-8") as f:
                json.dump({"minutes": int((uptime_ms - last) // 60000)}, f, ensure_ascii=False)
        except Exception:
            pass
        last = uptime_ms
    try:
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"uptime_ms": last}, f)
    except Exception as e:
        sys.stderr.write("counter write failed: %s\n" % e)


def cmd_prompt_submit(engine, fmt):
    """UserPromptSubmit: вклеить параметры при первом сообщении и при ИЗМЕНЕНИИ
    params/compass (mtime+размер). Гарантия: сообщение владельца обрабатывается
    вместе с актуальными значениями, даже если агент «не согласен».
    Всегда — строка Kit; при смене версии кита — громкий блок первым."""
    ev = read_stdin_json()
    sid = session_id_of(ev)
    orchlib.touch_session(sid)
    p = orchlib.load_params()
    orchlib.seed_session_compass(p, sid)
    if not enabled(p, sid):
        return
    kit_ver, kit_update = kit_paste_for_session(sid)
    kit_line = "Kit: %s" % kit_ver
    # гард: живое превышение ИЛИ pending-флаг (сессия / state) → громкий блок
    # (после блока обновления кита, если он есть)
    live_ov = orchlib.compass_overflows(p)
    flag_g = os.path.join(orchlib.session_dir(sid), "pending_compass_guard.json")
    pending_ov = None
    try:
        with open(flag_g, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            pending_ov = payload.get("overflows")
        elif isinstance(payload, list):
            pending_ov = payload
    except Exception:
        pass
    flag_st = os.path.join(orchlib.find_state_dir(), "pending_compass_guard.json")
    state_ov = None
    if not live_ov and pending_ov is None:
        try:
            with open(flag_st, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                state_ov = payload.get("overflows")
            elif isinstance(payload, list):
                state_ov = payload
        except Exception:
            pass
    if live_ov:
        guard = orchlib.format_compass_overflows(live_ov)
    elif pending_ov:
        guard = orchlib.format_compass_overflows(pending_ov)
    elif state_ov:
        guard = orchlib.format_compass_overflows(state_ov)
    else:
        guard = ""
    # снять pending-флаг после доставки (как nudge); живое превышение и так
    # поймает следующий prompt-submit. Сняли и если превышение уже ушло.
    if pending_ov is not None:
        try:
            os.unlink(flag_g)
        except Exception:
            pass
    if state_ov is not None:
        try:
            os.unlink(flag_st)
        except Exception:
            pass
    marks = {}
    for name, path in (("params", orchlib.params_file()),
                       ("compass", compass_of(p, sid))):
        try:
            st = os.stat(path)
            marks[name] = "%d:%d" % (st.st_mtime_ns if hasattr(os.stat_result, "st_mtime_ns") else int(st.st_mtime), st.st_size)
        except OSError:
            marks[name] = "none"
    mf = os.path.join(orchlib.find_state_dir(), "counters", "prompt-submit-%s.json" % safe_name(sid))
    os.makedirs(os.path.dirname(mf), exist_ok=True)
    prev = {}
    try:
        with open(mf, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        pass
    # доставка нуджа, накопленного heartbeat (Kimi): флажок -> текст, сброс
    flag = os.path.join(orchlib.session_dir(sid), "pending_nudge.json")
    nudge = ""
    try:
        with open(flag, "r", encoding="utf-8") as f:
            pending = json.load(f)
        nudge = NUDGE_TEXT.format(
            minutes=pending.get("minutes", 0), calls=0,
            summary=orchlib.params_summary(p),
            compass=compass_of(p, sid),
        )[:4000] + "\n---\n"
        os.unlink(flag)
    except Exception:
        pass

    # префикс: kit_update → compass-guard → детекторы → чужой state (все emit)
    kit_prefix = kit_update if kit_update else ""
    guard_prefix = (guard + "\n") if guard else ""
    det_lines = []
    try:
        bad_orders = orchlib.orders_without_basis()
    except Exception:
        bad_orders = []
    try:
        bad_waves = orchlib.waves_without_prosecutor()
    except Exception:
        bad_waves = []
    for path in bad_orders or []:
        det_lines.append(
            '⛔ ПРИКАЗ БЕЗ ОБОСНОВАНИЯ: %s — добавь строку "подход: ..." '
            'или "без советников: выбора нет"' % path)
    for fid in bad_waves or []:
        det_lines.append(
            "⚠️ ВОЛНА БЕЗ ПРОКУРОРА: фронт %s — работы идут, записи "
            "прокурора в журнале нет; запусти прокурора волной и не "
            "принимай волну без него" % fid)
    det_prefix = ("\n".join(det_lines) + "\n") if det_lines else ""
    foreign = foreign_state_warning()
    foreign_prefix = (foreign + "\n") if foreign else ""
    head = kit_prefix + guard_prefix + det_prefix + foreign_prefix

    if not guard and not nudge and prev == marks and not kit_update:
        # params/compass не менялись — всё равно вклеиваем Kit (всегда)
        emit(fmt, "UserPromptSubmit", (head + kit_line)[:9500] if head else kit_line[:9500])
        return
    if not nudge and prev == marks and not kit_update:
        # только превышения — громкий блок + Kit без сводки params
        emit(fmt, "UserPromptSubmit", (head + kit_line)[:9500])
        return
    changed = [n for n in marks if prev.get(n) != marks[n]]
    what = " (изменились: %s)" % ", ".join(changed) if prev else ""
    cpath_ps = compass_of(p, sid)
    if os.path.exists(cpath_ps):
        compass_hint = ("%s (создан из общего шаблона — заполни Цель/TODO "
                        "при первой задаче)") % cpath_ps
    else:
        compass_hint = "%s (СОЗДАЙ при первой задаче)" % cpath_ps
    text = (
        "Сессия: {sid}. Compass этой сессии: {compass}\n"
        "{map_orient}\n"
        "Актуальные параметры пачки{what}:\n{summary}\n"
        "{kit_line}\n"
        "Задание: {compass}\n"
        "Эти значения — из файла; следующее сообщение владельца обрабатывается "
        "с ними. Расхождение с ними — ошибка курса."
    ).format(sid=sid, what=what, summary=orchlib.params_summary(p),
             compass=compass_hint, map_orient=_MAP_ORIENT, kit_line=kit_line)
    emit(fmt, "UserPromptSubmit", (head + nudge + text)[:9500])
    try:
        with open(mf, "w", encoding="utf-8") as f:
            json.dump(marks, f)
    except Exception as e:
        sys.stderr.write("marker write failed: %s\n" % e)


def main():
    orchlib.utf8_stdio()
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(__doc__ + "\n")
        return 0
    cmd = args[0]
    engine, fmt = "claude", "json"
    rest = args[1:]
    i = 0
    while i < len(rest):
        if rest[i] == "--engine":
            engine = rest[i + 1]; i += 2
        elif rest[i] == "--format":
            fmt = rest[i + 1]; i += 2
        else:
            i += 1
    if engine == "kimi":
        fmt = "text"
    try:
        if cmd == "session-start":
            cmd_session_start(engine, fmt)
        elif cmd == "post-tool":
            cmd_post_tool(engine, fmt)
        elif cmd == "pre-tool":
            return cmd_pre_tool(engine, fmt)
        elif cmd == "subagent-start":
            return cmd_subagent_lifecycle("start")
        elif cmd == "subagent-stop":
            return cmd_subagent_lifecycle("end")
        elif cmd == "prompt-submit":
            cmd_prompt_submit(engine, fmt)
        elif cmd == "heartbeat":
            cmd_heartbeat(engine, fmt)
        elif cmd == "stop":
            pass  # зарезервировано: молчим, не мешаем завершению turn
        else:
            sys.stderr.write("unknown subcommand: %s\n" % cmd)
            return 2
    except Exception as e:
        sys.stderr.write("reground error: %s\n" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
