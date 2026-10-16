#!/usr/bin/env python3
"""ChatGPT web2api — NO BROWSER. Pure Python (curl_cffi TLS impersonation) + Node/jsdom for sentinel VM.

Pipeline:
 1. curl_cffi (Firefox TLS) -> CF cookies
 2. POST /backend-api/sentinel/req with fingerprint proof -> chatReq (token, PoW seed, turnstile.dx)
 3. node sentinel_jsdom3.js runs OpenAI's own sentinel SDK -> turnstile token `t`
 4. POST /f/conversation/prepare -> conduit_token
 5. POST /f/conversation with all tokens -> SSE stream
"""
import asyncio, json, time, uuid, base64, argparse, itertools, os, sys, subprocess
from curl_cffi.requests import AsyncSession

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:157.0) Gecko/20100101 Firefox/157.0"
BASE = "https://chatgpt.com"
DIR = os.path.dirname(os.path.abspath(__file__))
JSDOM_DIR = DIR  # has node_modules with jsdom

SALT = "52d97de1678a59c48595aee63945b2de5593dba7"

def fp_prep(now, uid, cfg):
    return [3000, time.strftime("%a %b %d %Y %H:%M:%S GMT+0300 (Москва, стандартное время)"),
            None, 1, cfg["user_agent"], None, SALT, "ru-RU", "ru-RU,ru,en-US,en", 0,
            "getGamepads−function getGamepads() {\n    [native code]\n}",
            "location", "event", 3086, uid, "", 8, now, 0, 0, 0, 0, 0, 1, 1]

def fp_final(now, uid, cfg):
    return [3000, time.strftime("%a %b %d %Y %H:%M:%S GMT+0300 (Москва, стандартное время)"),
            None, 1, cfg["user_agent"], "ru-RU", "ru-RU,ru,en-US,en", 2, "js?onload=jsdOnload",
            SALT, "ru-RU", "ru-RU,ru,en-US,en", 2,
            "taintEnabled−function taintEnabled() {\n    [native code]\n}",
            "__reactContainer$fhoa4vqqljp", "navigator", 4117, uid, "", 8, now, 0, 0, 0, 0, 0, 1, 1]


def fnv(b):
    h = 0x811c9dc5
    for c in b:
        h ^= c
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h

def proof(arr):
    raw = json.dumps(arr, ensure_ascii=False)
    return "gAAAAA" + base64.b64encode(raw.encode()).decode()


class Account:
    def __init__(self, cfg):
        self.name = cfg.get("name", "anon")
        self.cfg = cfg
        self.session = None
        self.lock = asyncio.Lock()
        self.busy = False
        self.fail_count = 0
        self.warm = False

    async def init(self):
        self.session = AsyncSession(impersonate="firefox135")
        r = await self.session.get(BASE + "/", headers={"user-agent": UA})
        did = self.cfg.get("oai_did")
        if did:
            self.session.cookies.set("oai-did", did, domain="chatgpt.com", path="/")
        print(f"[+] {self.name}: home {r.status_code}")
        # verify auth if token given
        tok = self.cfg.get("access_token")
        if tok:
            H = self.headers()
            r = await self.session.get(BASE + "/backend-api/me", headers=H)
            print(f"[+] {self.name}: me {r.status_code}")
        self.warm = True

    def headers(self):
        H = {"authorization": f"Bearer {self.cfg['access_token']}",
             "oai-language": "ru-RU", "originator": "Codex Browser",
             "origin": BASE, "referer": BASE + "/", "user-agent": UA,
             "content-type": "application/json", "accept": "*/*",
             "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
             "x-openai-web-frontend": "codex_webview",
             "x-openai-codex-window-type": "browser",
             "x-oai-mcp-form-version": "2",
             "oai-echo-logs": "",
             "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
             "sec-fetch-site": "same-origin", "priority": "u=0"}
        if self.cfg.get("oai_did"):
            H["oai-did"] = self.cfg["oai_did"]
        if self.cfg.get("account_id"):
            H["chatgpt-account-id"] = self.cfg["account_id"]
        return H

    async def _run_vm(self, chatreq: dict):
        uid = str(uuid.uuid4())
        if not os.path.isdir(os.path.join(JSDOM_DIR, "node_modules", "jsdom")):
            raise RuntimeError(
                "jsdom not installed — run `npm install` in the project directory")
        proc = await asyncio.create_subprocess_exec(
            "node", os.path.join(DIR, "sentinel_jsdom3.js"),
            cwd=JSDOM_DIR,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(
                (json.dumps(chatreq) + "\ndummy\n" +
                 self.cfg.get("oai_did", "") + "\n" + uid + "\n").encode()), timeout=45)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("sentinel VM timeout (45s)")
        line = out.decode().strip().split("\n")[-1]
        if not line:
            raise RuntimeError("sentinel VM produced no output: "
                               + err.decode()[-500:])
        if line.startswith("ERR:"):
            raise RuntimeError(f"sentinel VM: {line}")
        try:
            tok = json.loads(line)
        except json.JSONDecodeError:
            raise RuntimeError("sentinel VM bad output: "
                               + (line[:200] or err.decode()[-300:]))
        return tok, uid

    async def sentinel_token(self):
        # pass A: sentinel.openai.com req tokens (needed to talk to chat-requirements)
        H = self.headers()
        tokA, uid = await self._run_vm({"flow": {"flow": "chat"}})
        HA = dict(H)
        HA["openai-sentinel-chat-requirements-token"] = tokA.get("c", "")
        HA["openai-sentinel-proof-token"] = tokA.get("p", "")
        if tokA.get("t"):
            HA["openai-sentinel-turnstile-token"] = tokA["t"]
        r = await self.session.post(BASE + "/backend-api/sentinel/chat-requirements",
                                    headers=HA, json={})
        if r.status_code != 200:
            raise RuntimeError(f"chat-requirements {r.status_code}: {r.text[:200]}")
        chatreq = r.json()
        # pass B: SDK enforcement tokens against THIS chatReq (intercepted fetch)
        tokB, _ = await self._run_vm(chatreq)
        return {"requirements": chatreq, "turnstile_bundle": tokB,
                "proof": tokB.get("p") or "", "uid": uid}

    async def ask_stream(self, prompt: str, timeout_s: int = 180):
        """yields text deltas"""
        H = self.headers()
        now = int(time.time() * 1000)
        uid = str(uuid.uuid4())

        # 1. sentinel tokens
        st = await self.sentinel_token()
        req = st["requirements"]
        bundle = st["turnstile_bundle"]

        req = st["requirements"]
        H["openai-sentinel-chat-requirements-token"] = req.get("token", "")
        H["openai-sentinel-proof-token"] = bundle.get("p") or st["proof"]
        H["openai-sentinel-turnstile-token"] = bundle.get("t") or ""
        H["x-openai-web-sse-compression"] = "gzip"
        H["x-oai-turn-trace-id"] = str(uuid.uuid4())

        # 2. conversation prepare -> conduit token
        prepb = {"client_prepare_state": "sent", "action": "next",
                 "is_do_not_remember": False, "model": "auto",
                 "parent_message_id": "client-created-root",
                 "timezone": "Europe/Moscow", "timezone_offset_min": -180,
                 "local_function_names": ["local.continue_in_work"],
                 "partial_query": {"author": {"role": "user"},
                                   "content": {"content_type": "text", "parts": [prompt]},
                                   "id": str(uuid.uuid4())},
                 "client_prepare_dispatch": "debounced",
                 "client_prepare_source": "composer_editor_state"}
        r = await self.session.post(BASE + "/backend-api/f/conversation/prepare",
                                    headers=H, json=prepb)
        if r.status_code != 200:
            raise RuntimeError(f"prepare {r.status_code}: {r.text[:200]}")
        H["x-conduit-token"] = r.json()["conduit_token"]

        # 3. main conversation
        # bundle keys: p (enforcement proof), t (turnstile), c (requirements token), flow
        H["openai-sentinel-chat-requirements-token"] = req["token"] or st["requirements"].get("token")
        H["openai-sentinel-proof-token"] = bundle.get("p") or st["proof"]
        H["openai-sentinel-turnstile-token"] = bundle.get("t") or ""
        H["x-openai-web-sse-compression"] = "gzip"
        H["x-oai-turn-trace-id"] = str(uuid.uuid4())
        body = {"turn_attribution": {"turn_trigger": "composer"}, "action": "next",
                "is_do_not_remember": False, "model": "auto",
                "parent_message_id": "client-created-root",
                "timezone": "Europe/Moscow", "timezone_offset_min": -180,
                "client_contextual_info": {"app_name": "chatgpt.com",
                                           "app_surface": "codex_browser",
                                           "has_web_push_capabilities": True,
                                           "web_push_notification_permission": "default"},
                "local_function_names": ["local.continue_in_work"],
                "messages": [{"author": {"metadata": {}, "name": None, "role": "user"},
                              "channel": None,
                              "content": {"content_type": "text", "parts": [prompt]},
                              "create_time": time.time(), "end_turn": None,
                              "id": str(uuid.uuid4()), "metadata": {}, "recipient": "all",
                              "status": "finished_successfully", "update_time": None,
                              "weight": 1}],
                "supported_encodings": ["v1"], "genui_state_snapshots": [],
                "client_prepare_state": "success"}
        r = await self.session.post(BASE + "/backend-api/f/conversation",
                                    headers=H, json=body, stream=True)
        if r.status_code != 200:
            raise RuntimeError(f"conversation {r.status_code}: {r.text[:200]}")
        # 4. parse SSE stream
        buf = ""
        async for chunk in r.aiter_content():
            buf += chunk.decode("utf-8", "replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                s = line[6:]
                try:
                    d = json.loads(s)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get("type") == "resume_conversation_token":
                    continue
                v = d.get("v")
                if isinstance(v, str) and v and d.get("o") in ("append", "add"):
                    path = d.get("p", "")
                    if "parts" in path or path == "":
                        yield v
                if isinstance(v, list):  # patch op: list of sub-deltas
                    for sub in v:
                        if (isinstance(sub, dict) and sub.get("o") == "append"
                                and isinstance(sub.get("v"), str)
                                and "parts" in (sub.get("p") or "")):
                            yield sub["v"]
                if isinstance(v, dict):
                    msg = v.get("message") or {}
                    if msg.get("author", {}).get("role") == "assistant":
                        parts = (msg.get("content") or {}).get("parts") or []
                        for p in parts:
                            if isinstance(p, str) and p:
                                yield p


def load_accounts(path):
    if not os.path.exists(path):
        json.dump({"accounts": [{"name": "anon1", "access_token": None}]},
                  open(path, "w"), indent=2)
    data = json.load(open(path))
    return [Account(c) for c in data.get("accounts", [])]


class Pool:
    def __init__(self, accounts):
        self.accounts = [a for a in accounts]
        self.rr = itertools.cycle(accounts)

    def next(self):
        n = len(self.accounts)
        for _ in range(n):
            a = next(self.rr)
            if not a.busy:
                return a
        return next(self.rr)

    async def ask(self, prompt, timeout_s=180):
        acc = self.next()
        acc.busy = True
        try:
            async with acc.lock:
                async for delta in acc.ask_stream(prompt, timeout_s):
                    yield acc.name, delta
        except Exception as e:
            acc.fail_count += 1
            raise
        finally:
            acc.busy = False


async def run_server(pool, port):
    from aiohttp import web

    def sse(delta=None, finish=None, model="chatgpt-web", cid=None, usage=None, tool_calls=None):
        d = {}
        if tool_calls is not None:
            d = {"role": "assistant", "tool_calls": tool_calls}
        elif finish is None:
            d = {"role": "assistant"} if delta == "" else ({"content": delta} if delta is not None else {})
        o = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
             "model": model, "choices": [{"index": 0, "delta": d, "finish_reason": finish}]}
        if usage: o["usage"] = usage
        return "data: " + json.dumps(o) + "\n\n"

    def build_prompt(msgs, tools=None, tool_call_active=False):
        parts = []
        tool_block = None
        if tools:
            tl = []
            for t in tools:
                f = t.get("function", t)
                tl.append({"name": f.get("name"),
                           "description": f.get("description", ""),
                           "parameters": f.get("parameters", {})})
            tool_block = (
                "[TOOLS — STRICT PROTOCOL] You have real function-calling tools connected. "
                "To call a tool, reply with ONLY this JSON and nothing else: "
                '{"tool_calls": [{"name": "<tool>", "arguments": {...}}]} '
                "(no markdown, no explanation, no other text). The runtime will execute it and "
                "send back the result. You do NOT have the data yourself — you CANNOT run "
                "commands, read files or know the current time without calling a tool. "
                "If the user asks anything requiring a tool, you MUST call it. Only answer "
                "directly when no tool is needed. Tools:\n"
                + json.dumps(tl, ensure_ascii=False))
        if tool_call_active:
            parts.append("[Tool results received] Use them to answer the user. "
                         "You may call more tools with the same JSON format if needed.")
        for m in msgs:
            role, content = m.get("role"), m.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            if role == "system":
                parts.append(f"[System instructions]: {content}")
            elif role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                tc = m.get("tool_calls")
                if tc:
                    calls = []
                    for c in tc:
                        fn = c.get("function", {})
                        calls.append({"name": fn.get("name"),
                                      "arguments": fn.get("arguments", "")})
                    parts.append(f"Assistant (tool call): {json.dumps(calls, ensure_ascii=False)}")
                else:
                    parts.append(f"Assistant: {content}")
            elif role == "tool":
                parts.append(f"[Tool {m.get('name','') or 'tool'} result]: {content}")
        if tool_block and not tool_call_active:
            parts.append(tool_block)
            parts.append("ВАЖНО: перечисленные выше инструменты РЕАЛЬНО подключены к этой сессии. "
                         "Если для ответа нужен инструмент (команда, файл, время и т.п.) — "
                         "ответь ТОЛЬКО валидным JSON вида {\"tool_calls\": [{\"name\": ..., "
                         "\"arguments\": {...}}]} без какого-либо другого текста. Не пиши, что "
                         "у тебя нет инструментов — они есть.")
        parts.append("Assistant:")
        return "\n\n".join(parts)

    async def completions(request):
        body = await request.json()
        msgs = body.get("messages", [])
        tools = body.get("tools") or []
        # detect tool-result phase: last message role == "tool"
        tool_call_active = bool(msgs) and msgs[-1].get("role") == "tool"
        prompt = build_prompt(msgs, tools=tools, tool_call_active=tool_call_active)
        stream = bool(body.get("stream"))
        model = body.get("model", "chatgpt-web")
        cid = "chatcmpl-" + str(uuid.uuid4())
        zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        async def collect():
            parts, acc = [], None
            async for name, delta in pool.ask(prompt):
                parts.append(delta); acc = name
            return "".join(parts), acc

        def parse_tool_calls(text):
            """model may answer with {"tool_calls":[{name,arguments}]} JSON"""
            t = text.strip()
            # strip code fences
            if t.startswith("```"):
                t = t.strip("`")
                if t.startswith("json"):
                    t = t[4:]
                t = t.strip()
            if not t.startswith("{"):
                return None
            try:
                j = json.loads(t)
            except json.JSONDecodeError:
                # try to find the last balanced JSON object in text
                i = text.find('{"tool_calls"')
                if i < 0:
                    return None
                depth = 0
                for k in range(i, len(text)):
                    if text[k] == "{":
                        depth += 1
                    elif text[k] == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                j = json.loads(text[i:k+1])
                            except json.JSONDecodeError:
                                return None
                            break
                else:
                    return None
            calls = j.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                return None
            out = []
            for n, c in enumerate(calls):
                if not isinstance(c, dict):
                    continue
                name = c.get("name") or c.get("tool") or ""
                args = c.get("arguments", c.get("args", "{}"))
                if not name:
                    # salvage: single unknown-name entry → still return with placeholder
                    # so the runtime returns an error and the model corrects itself
                    name = "unknown_tool"
                if isinstance(args, dict) and "arguments" in args and len(args) == 1:
                    args = args["arguments"]  # model wrapped args twice
                out.append({
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": name,
                                 "arguments": args if isinstance(args, str)
                                              else json.dumps(args, ensure_ascii=False)}})
            return out or None

        async def collect_with_retry():
            text, acc = await collect()
            if tools and not tool_call_active and parse_tool_calls(text) is None:
                # up to 2 nudges — web model randomly denies having tools
                for attempt in range(2):
                    retry_prompt = (prompt
                                    + "\n\n[System] Твой предыдущий ответ был НЕВАЛИДНЫМ "
                                    "JSON вызова инструмента: " + repr(text[:200])
                                    + '\nПравильный формат СТРОГО: {"tool_calls": '
                                    '[{"name": "<имя инструмента из списка>", '
                                    '"arguments": {<аргументы по схеме>}}]} '
                                    "— одно имя из списка, аргументы строго по parameters. "
                                    "Ответь заново ТОЛЬКО валидным JSON.")
                    parts2 = []
                    acc2 = None
                    async for name, delta in pool.ask(retry_prompt):
                        parts2.append(delta); acc2 = name
                    t2 = "".join(parts2)
                    if parse_tool_calls(t2):
                        return t2, acc2
            return text, acc

        if not stream:
            text, acc = await collect_with_retry()
            tcalls = parse_tool_calls(text) if tools else None
            if tcalls:
                return web.json_response({
                    "id": cid, "object": "chat.completion", "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant", "content": None,
                                             "tool_calls": tcalls},
                                 "finish_reason": "tool_calls"}],
                    "usage": zero, "_account": acc})
            return web.json_response({
                "id": cid, "object": "chat.completion", "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": zero, "_account": acc})

        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-cache",
            "Connection": "keep-alive", "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*"})
        await resp.prepare(request)
        try:
            await resp.write(sse(delta="", model=model, cid=cid).encode())
            text, _ = await collect_with_retry()
            tcalls = parse_tool_calls(text) if tools else None
            if tcalls:
                await resp.write(sse(delta=None, model=model, cid=cid,
                                     tool_calls=tcalls,
                                     finish="tool_calls", usage=zero).encode())
            else:
                if text:
                    await resp.write(sse(delta=text, model=model, cid=cid).encode())
                await resp.write(sse(finish="stop", model=model, cid=cid, usage=zero).encode())
            await resp.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        await resp.write_eof()
        return resp

    async def options(request):
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type"})

    async def models(request):
        return web.json_response({"object": "list", "data": [
            {"id": "chatgpt-web", "object": "model", "owned_by": "openai"},
            {"id": "gpt-4o", "object": "model", "owned_by": "openai"}]})

    async def health(request):
        return web.json_response({"accounts": [{"name": a.name, "busy": a.busy,
                                                "fails": a.fail_count} for a in pool.accounts]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completions)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/health", health)
    app.router.add_route("OPTIONS", "/v1/chat/completions", options)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    print(f"[*] NO-BROWSER server on http://127.0.0.1:{port}/v1/chat/completions")
    print(f"[*] health: http://127.0.0.1:{port}/health")
    while True:
        await asyncio.sleep(3600)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--accounts", default=os.path.join(DIR, "accounts_nb.json"))
    ap.add_argument("--once", metavar="PROMPT", help="single prompt, print answer, exit")
    args = ap.parse_args()

    accounts = load_accounts(args.accounts)
    print(f"[*] {len(accounts)} account(s)")
    for a in accounts:
        try:
            await a.init()
        except Exception as e:
            print(f"[!] {a.name} init failed: {e}", file=sys.stderr)
    alive = [a for a in accounts if a.warm]
    if not alive:
        print("[!] no accounts", file=sys.stderr); return

    if args.once:
        pool = Pool(alive)
        async for name, delta in pool.ask(args.once):
            print(delta, end="", flush=True)
        print()
        return

    await run_server(Pool(alive), args.port)


if __name__ == "__main__":
    asyncio.run(main())
