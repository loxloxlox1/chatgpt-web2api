# Подключение CLI-клиентов к chatgpt_server.py

Сервер: `xvfb-run -a python3 chatgpt_server.py --port 8080`
Endpoint: `http://127.0.0.1:8080/v1` (OpenAI-совместимый)

---

## Hermes

```bash
export OPENAI_API_BASE=http://127.0.0.1:8080/v1
export OPENAI_API_KEY=sk-dummy        # любой, не проверяется
hermes chat
```

или в конфиге hermes (`~/.hermes/config.json` или env):
```json
{
  "providers": {
    "chatgpt-web": {
      "base_url": "http://127.0.0.1:8080/v1",
      "api_key": "sk-dummy",
      "model": "chatgpt-web"
    }
  }
}
```

## shell-gpt (sgpt)

```bash
export OPENAI_API_KEY=sk-dummy
export OPENAI_BASE_URL=http://127.0.0.1:8080/v1
sgpt "привет"
```

## llm (Simon Willison CLI)

```bash
llm keys set openai   # ввести: sk-dummy
llm chat --model chatgpt-web
# но сначала скажи llm про кастомный base_url:
mkdir -p ~/.config/io.datasette.llm
cat > ~/.config/io.datasette.llm/extra_openai_models.json <<'EOF'
{
  "chatgpt-web": {
    "base_url": "http://127.0.0.1:8080/v1",
    "model_id": "chatgpt-web"
  }
}
EOF
```

## aide / continue.dev / cline (VS Code)

```json
{
  "openai": {
    "apiBase": "http://127.0.0.1:8080/v1",
    "apiKey": "sk-dummy",
    "model": "chatgpt-web"
  }
}
```

## Python (официальный SDK)

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="sk-dummy")

# стриминг
for chunk in client.chat.completions.create(
    model="chatgpt-web",
    messages=[{"role":"user","content":"привет"}],
    stream=True,
):
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

## curl

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"stream":true,"messages":[{"role":"user","content":"привет"}]}'
```

---

## Особенности

- **api_key игнорируется** — можно писать что угодно (`sk-dummy`).
- **model игнорируется** — всё уходит в ChatGPT web (`auto`). Имена `gpt-4o`, `gpt-4` и т.п. перечислены в `/v1/models` просто для совместимости.
- **system-сообщения** работают — передаются как `[System instructions]:` в начале промпта.
- **Мульти-тёрн** — вся история `messages` склеивается в диалог и отправляется одним сообщением.
- **Vision-контент** (`content: [{type:"text",...}]`) — текстовые части извлекаются, картинки игнорируются.
- **`/health`** — показывает состояние пула аккаунтов: `curl http://127.0.0.1:8080/health`

## No-browser сервер (без Firefox/Xvfb!)
```bash
python3 chatgpt_nobrowser.py --port 8082
# требует: curl_cffi, aiohttp, node + npm-пакет jsdom (в ./node_modules рядом с sentinel_jsdom3.js)
# файлы: chatgpt_nobrowser.py, sentinel_jsdom3.js, sdk3_fp.js, sdk3.js, accounts_nb.json
```
Тот же OpenAI-совместимый API: http://127.0.0.1:8082/v1/chat/completions
