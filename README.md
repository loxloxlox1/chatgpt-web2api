# ChatGPT web2api (no-browser)

Неофициальный **OpenAI-совместимый API-сервер** поверх веб-интерфейса ChatGPT.
**Без браузера** — ни Playwright, ни Firefox, ни Xvfb. Только HTTP + Node.

## Как это работает

```
клиент (Hermes/sgpt/llm/SDK/curl)
        │  OpenAI-формат /v1/chat/completions
        ▼
chatgpt_nobrowser.py  (aiohttp, SSE, round-robin аккаунты)
        │  curl_cffi (TLS-имперсация Firefox)
        ▼
chatgpt.com/backend-api/...
        ▲  sentinel-токены
sentinel_jsdom3.js    (Node + jsdom: запускает настоящий sentinel SDK
        │              в эмуляции браузера, решает PoW + turnstile VM)
        └── sdk3_fp.js (SDK с патченным fingerprint-сборщиком getConfig)
```

1. `curl_cffi` с TLS-отпечатком Firefox проходит Cloudflare.
2. Sentinel SDK OpenAI исполняется в **jsdom** (эмуляция DOM) — генерирует
   proof (hashcash по seed), turnstile-токен (их закрытая VM) и requirements-токен.
3. Запросы идут в `backend-api`: `sentinel/req` → `chat-requirements` →
   `f/conversation/prepare` (conduit) → `f/conversation` (SSE).
4. Ответ конвертируется обратно в OpenAI-формат (`chat.completion.chunk`).

## Установка

```bash
pip install curl_cffi aiohttp
npm install
```

## Настройка аккаунтов

`accounts_nb.json`:
```json
{
  "accounts": [
    {
      "name": "acc1",
      "access_token": "eyJhbGciOi...",   // Bearer из чата chatgpt.com
      "oai_did": "uuid",                  // кука oai-did (опционально)
      "account_id": "uuid",               // ChatGPT-Account-Id (опционально)
      "user_agent": "Mozilla/5.0 ..."     // любой Firefox UA
    }
  ]
}
```
Несколько аккаунтов → round-robin ротация с busy-локами.

## Запуск

```bash
python3 chatgpt_nobrowser.py --port 8082
# или CLI на один запрос:
python3 chatgpt_nobrowser.py --once "привет"
```

## Использование

```bash
curl http://127.0.0.1:8082/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"привет"}]}'
```

Конфиги для Hermes, sgpt, llm, aide/continue, Python SDK, curl — в [CLIENTS.md](CLIENTS.md).

```python
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8082/v1", api_key="sk-anything")
print(c.chat.completions.create(model="x",
      messages=[{"role":"user","content":"2+2?"}]).choices[0].message.content)
```

## Эндпоинты

- `POST /v1/chat/completions` — stream и non-stream, system-сообщения, мультитёрн
- `GET /v1/models`
- `GET /health` — состояние аккаунтов

## Файлы

| Файл | Что это |
|---|---|
| `chatgpt_nobrowser.py` | сервер + пайплайн запросов |
| `sentinel_jsdom3.js` | Node-раннер sentinel SDK в jsdom (перехват fetch, фейковый iframe) |
| `sdk3_fp.js` | sentinel SDK `20260810913b` с патченным `getConfig` (детерминированный fingerprint) |
| `sdk3.js` | чистый SDK (для пересборки при обновлении версии) |

## Важно

- **Неофициально**. Нарушает ToS OpenAI — аккаунт могут забанить. Используйте на свой риск.
- Sentinel SDK и форматы меняются: при поломке скачайте свежий
  `https://sentinel.openai.com/sentinel/<версия>/sdk.js` (версия видна в HAR/network)
  и заново наложите патч `getConfig` (см. `sdk3_fp.js`).
- Токены в `accounts_nb.json` — секреты, не коммитьте реальные значения.
