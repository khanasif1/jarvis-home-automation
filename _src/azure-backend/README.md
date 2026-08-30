# Azure streaming backend

This Python 3.11 Azure Function has three anonymous HTTP routes:

| Route | Purpose |
|---|---|
| `GET /api/health` | Function health probe; `?deep=true` also verifies managed identity and the Foundry Realtime handshake |
| `POST /api/voice/intent` | Authenticate and classify follow-up PCM as the fixed `JARVIS_QUERY` or `JARVIS_SLEEP` action |
| `POST /api/voice/stream` | Authenticate the fixed Pi UUID, consume 16 kHz PCM, and stream 24 kHz response PCM |

“Anonymous” is the Azure Functions auth level only. The voice route requires
the exact canonical UUID in `X-Device-Guid`; TLS protects it in transit.

Request chunks are validated and incrementally resampled from 16 kHz to the
24 kHz PCM required by GPT Realtime. The Function appends those chunks to one
request-scoped Foundry Realtime session, commits at request EOF, and waits for
the first valid audio delta before returning `200 audio/pcm`. Later deltas are
forwarded without buffering the complete response.

The intent route forces one of two structured Foundry function calls. Stop,
decline, goodbye, and unclear/noise-only follow-ups become `JARVIS_SLEEP`; only
a clear new request becomes `JARVIS_QUERY`. No transcript or conversation state
is retained. Classification uses minimal reasoning with a bounded 256-token
budget. If Foundry reports an incomplete response, the route accepts a fully
completed recognized tool call; otherwise it fails closed to `JARVIS_SLEEP`
instead of returning a transient `502` to the Pi.

The answer session is also instructed to ask for repetition when an important
word, name, number, or intent is unclear. It must not guess an unrelated answer
or calculation from ambiguous audio.

Azure authentication uses only the Function system-assigned managed identity:

- token scope: `https://ai.azure.com/.default`
- role: `Cognitive Services OpenAI User`
- no model API key
- identity-based `AzureWebJobsStorage`

The async managed-identity client requires `aiohttp`; it is an explicit runtime
dependency so a remote Functions build cannot omit the Azure async transport.

## Required settings

| Setting | Example |
|---|---|
| `DEVICE_GUID` | canonical lowercase UUID |
| `AZURE_OPENAI_ENDPOINT` | `https://RESOURCE.services.ai.azure.com` |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | `gpt-realtime-2` |
| `AZURE_OPENAI_VOICE` | `alloy` |
| `AZURE_CLIENT_USE_MANAGED_IDENTITY` | `true` in Azure; `false` for local developer identity |
| `FOUNDRY_RESPONSE_TIMEOUT_SECONDS` | `60` |
| `PYTHON_ENABLE_INIT_INDEXING` | `1` |

See the [OpenAPI contract](../contracts/openapi.yaml) for exact audio headers and
error bodies. Use `infra/scripts/backend_lifecycle.py install` for deployment;
it creates all app settings and RBAC.
