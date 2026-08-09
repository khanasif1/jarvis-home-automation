# azure-backend

Python v2 (programming model) Azure Functions backend for the home-assistant
voice project. This component is fully independent: it only depends on
`contracts/` as a design reference (never imported at runtime), never
imports anything from `pi-client/`, and can be deployed, tested, and run
without any other part of the monorepo present.

## What it does

`POST /api/voice-turn` is the primary contract endpoint (see
`contracts/openapi.yaml` / `contracts/schemas/voice-turn-*.json`). A device
sends either `text` or base64-encoded `audio`, the backend:

1. Authenticates the device's bearer token against the claimed `deviceId`.
2. Replays a cached response if the `Idempotency-Key` + request body were
   already processed, or rejects the request with `409` if the same key is
   reused with a different body.
3. Transcribes audio via Azure AI Speech STT when `audioBase64` is used.
4. Runs an explicit, bounded Azure OpenAI tool-calling loop
   (`src/home_assistant_api/ai/orchestrator.py`) against a fixed list of
   backend tools (todos, reminders, Google Calendar/Tasks, Gmail search).
5. Synthesizes an audio reply via Azure AI Speech TTS when the request was
   voice-based.
6. Returns a `VoiceTurnResponse` that mirrors
   `contracts/schemas/voice-turn-response.json` exactly.

`GET /api/health` returns exactly `{"status": "ok"}`, matching the strict
(`additionalProperties: false`) contract schema -- no extra diagnostic
fields are added to this route.

A few practical, non-contract endpoints support day-to-day operation and the
Pi client's reminder poller:

| Route | Auth | Purpose |
| --- | --- | --- |
| `POST /api/admin/devices` | `x-admin-api-key` | Register a device, returns its bearer token once |
| `GET /api/admin/devices` | `x-admin-api-key` | List registered devices |
| `GET /api/reminders/due?deviceId=` | device bearer token | List due, undelivered reminders |
| `POST /api/reminders/{reminder_id}/ack` | device bearer token | Acknowledge (mark delivered) a reminder |
| `GET /api/google/oauth/start?deviceId=` | `x-admin-api-key` | Get a Google consent URL for a device |
| `GET /api/google/oauth/callback` | none (Google redirects here) | Exchange the auth code and store credentials |

## Layout

```
azure-backend/
  function_app.py            # Azure Functions v2 entry point (thin route wiring only)
  host.json                   # Functions host configuration
  local.settings.example.json # Template for local.settings.json (never commit the real file)
  requirements.txt             # Production dependencies only
  requirements-dev.txt         # + pytest/pytest-cov, for local validation
  pytest.ini                   # pytest config: src on path, artifacts under source-root .test-artifacts/
  prompts/
    assistant_system.txt       # Assistant system prompt (plain text asset, ships with the app)
  src/home_assistant_api/
    config.py                  # Strict, lazily-validated environment configuration
    errors.py                  # AppError hierarchy (code/http_status/retryable per error)
    auth.py                    # Device bearer-token + admin API key authentication
    models.py                  # Pydantic models mirroring the wire contract exactly
    time_utils.py               # UTC datetime helpers + Stopwatch
    telemetry.py                 # Structured logging / telemetry wrapper
    app_context.py               # Composition root wiring all dependencies
    routes.py                    # HTTP handlers (testable without a running host)
    repositories/                # In-memory todos/reminders/sessions/devices/idempotency stores
    speech/                      # Azure AI Speech STT/TTS REST adapters
    google/                      # Google OAuth, credential storage, Calendar/Tasks/Gmail clients
    tools/                       # Assistant tool implementations
    ai/                           # Tool definitions, tool executor, Azure OpenAI orchestrator
  tests/
    conftest.py                  # Shared fixtures + fakes (FakeChatClient, app_context_factory, ...)
    unit/                        # Unit tests, one module per source file
    integration/                 # End-to-end route tests using azure.functions.HttpRequest
```

## Local development

Commands in this document assume the current directory is the repository's
`_src/` source root.

1. Copy the settings template and fill in real values for whichever
   integrations you want to exercise locally (leave others blank -- see
   "Configuration" below for what happens when a setting is missing):

   ```powershell
   Copy-Item azure-backend/local.settings.example.json azure-backend/local.settings.json
   ```

2. Install dependencies (ideally in a virtual environment) and run the
   Functions host:

   ```powershell
   python -m pip install -r azure-backend/requirements-dev.txt
   cd azure-backend
   func start
   ```

   `func start` requires the [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
   to be installed separately; it is not a Python dependency.

## Running tests

Tests never require cloud credentials, a running Functions host, or network
access -- they exercise the in-memory repositories and inject explicit fakes
for Azure OpenAI (`FakeChatClient`), Azure Speech (fake `requests.Session`),
and Google APIs (fake service doubles) via `home_assistant_api.app_context.AppContext`
constructor overrides.

From the `_src/` source root:

```powershell
python -m pip install -r azure-backend/requirements-dev.txt
python -m pytest azure-backend/tests --basetemp=.test-artifacts/pytest/backend
python -m compileall -q azure-backend/function_app.py azure-backend/src
```

`pytest.ini` also sets `pythonpath = src`, so `pytest azure-backend/tests`
works from the source root without manually exporting `PYTHONPATH`. All
generated pytest cache/artifacts are written under the source-root
`.test-artifacts/` directory, never inside `azure-backend/` itself.

## Configuration reference

All configuration is read from environment variables (Azure Function App
settings in production, `local.settings.json` locally) via
`home_assistant_api.config.AppConfig`. Construction never fails; each
dependency is validated lazily the first time a code path needs it, so the
app starts and `/health` responds even with nothing configured. A missing
required setting raises `ConfigurationError` (HTTP 500) naming the variable,
never a silent fallback or an empty/fake result.

| Variable | Required for | Notes |
| --- | --- | --- |
| `APP_ENVIRONMENT` | - | `development` (default) or `production` |
| `DEVICE_API_TOKENS` | device bootstrap | JSON object `{"deviceId": "token"}`; optional bootstrap, devices can also be registered live |
| `ADMIN_API_KEY` | admin/device-management routes | Opaque string compared with `hmac.compare_digest` |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` | `/api/voice-turn` | `AZURE_OPENAI_API_VERSION` optional, defaults to `2024-10-21`; infra deploys `AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini` (model version `2025-04-14`) |
| `SPEECH_REGION`, `SPEECH_API_KEY` | audio-based voice turns | `SPEECH_DEFAULT_VOICE` optional, defaults to `en-US-JennyNeural` |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` | Google Calendar/Tasks/Gmail tools + OAuth endpoints | `GOOGLE_OAUTH_SCOPES` optional (space-separated); Google tools raise `ConfigurationError` per-device/per-call when unset instead of silently no-oping |
| `IDEMPOTENCY_TTL_SECONDS` | - | Default `86400` (24h) |
| `MAX_TOOL_ITERATIONS` | - | Default `5`; bounds the assistant tool-call loop, raising `UpstreamServiceError` if exceeded |
| `REMINDER_POLL_LOOKAHEAD_SECONDS` | - | Default `0`; reserved for future poller lookahead behavior |
| `ASSISTANT_SYSTEM_PROMPT_PATH` | - | Optional override of the default `prompts/assistant_system.txt` path |
| `PERSISTENCE_MODE` | - | `memory` or `table`; defaults to `table` in production, `memory` in development (see below) |
| `STORAGE_TABLE_ENDPOINT` | table persistence in production | Storage account table endpoint (e.g. `https://<account>.table.core.windows.net`); authenticated with `ManagedIdentityCredential` in production (`DefaultAzureCredential` only for opt-in local dev) -- **required** in production, never a connection string |
| `TABLE_STORAGE_CONNECTION_STRING` | table persistence in development | Explicit local/Azurite opt-in (e.g. `UseDevelopmentStorage=true`); ignored in production |
| `OAUTH_STATE_SIGNING_KEY` | Google OAuth CSRF protection | HMAC key signing the OAuth `state` parameter; falls back to `ADMIN_API_KEY` when unset |

### Persistence: in-memory vs. Azure Table Storage

Every repository (todos, reminders, sessions, devices, idempotency, Google
credentials) has two implementations behind the same interface:

- **In-memory** (`InMemory*Repository`): process-local, used by default in
  `development` and by every unit/integration test. No external dependency.
- **Table Storage** (`Table*Repository`, `repositories/table_storage.py`):
  durable, used by default in `production`. Reads/writes the exact tables
  and entity shape `infra/` provisions -- `Devices` (PartitionKey `device`,
  RowKey = device UUID, `DeviceName`/`TokenHash`/`Enabled`/`CreatedAtUtc`/
  `LastSeenAtUtc`), plus `Todos`, `Reminders`, `Sessions`, `Idempotency`,
  and `GoogleCredentials`. A device created by
  `infra/scripts/provision-device.*` is immediately usable without any
  extra migration step. Production relies on every table being
  IaC-provisioned ahead of time (`infra/modules/storage.bicep`'s
  `tableNames`); the repositories' own idempotent create-on-first-use
  (`ensure_table_exists`) is a harmless no-op against an already-existing
  table and exists mainly as a convenience for local/Azurite development,
  where nothing pre-provisions tables.

`AppConfig.persistence_mode` selects which set is composed by
`AppContext`; an explicit `PERSISTENCE_MODE` always wins, otherwise
production defaults to `table` and development to `memory` (development can
still opt into `table`, for example against Azurite, to exercise the
durable path locally).

Table Storage authentication (`AppConfig.require_table_storage_credential`)
has two mutually-exclusive shapes:

- **Identity-based** (`STORAGE_TABLE_ENDPOINT`): the only shape accepted
  in production. `infra/` grants the Function App's managed identity the
  `Storage Table Data Contributor` role on the storage account
  (`infra/modules/role-assignments.bicep`) and sets
  `STORAGE_TABLE_ENDPOINT` automatically (`infra/modules/function-app.bicep`).
  The provisioned storage account also disables shared-key access
  entirely (`allowSharedKeyAccess: false` in `infra/modules/storage.bicep`),
  so a connection string could never authenticate against it even if one
  were configured -- production requires `STORAGE_TABLE_ENDPOINT` and fails
  fast with `ConfigurationError` if it is absent, rather than falling back
  to a connection string or to in-memory persistence. The token credential
  used for this endpoint-based mode is chosen deterministically: in
  production it is always `ManagedIdentityCredential` (the Function App's
  own managed identity); `DefaultAzureCredential` is used only when this
  mode is opted into for local development against a real Azure Storage
  account (for example via `az login`), never in production.
- **Connection-string-based** (`TABLE_STORAGE_CONNECTION_STRING`): an
  explicit local-development opt-in, for example against the Azurite
  emulator (`UseDevelopmentStorage=true`). Never used in production.

`AppContext` accepts an `azure_credential_factory` override so tests can
exercise the identity-based composition path with a fake token credential,
never a real `ManagedIdentityCredential`/`DefaultAzureCredential`/network
call.

> **Note:** `AzureWebJobsStorage` is the Azure Functions *host runtime's*
> own storage setting (required by the Functions host itself for
> Flex Consumption/triggers bookkeeping) -- on Flex Consumption with
> identity-based storage it is expressed as `AzureWebJobsStorage__blobServiceUri`
> / `__queueServiceUri` / `__tableServiceUri` app settings rather than a
> connection string, and is entirely separate from this application's own
> persistence. This backend's repositories never read `AzureWebJobsStorage`
> (in any form) and never require a connection string in production; they
> exclusively consume the application-level `STORAGE_TABLE_ENDPOINT` /
> `TABLE_STORAGE_CONNECTION_STRING` settings described above. The
> `AzureWebJobsStorage` entry in `local.settings.example.json` is only for
> the local Functions Core Tools host when running `func start`.

## Deployment package contents

Running `infra/scripts/backend_lifecycle.py install` from `_src/` invokes
`azd up`, which provisions the services and packages this directory as-is
except for the paths listed in `.funcignore`
(`tests/`, `requirements-dev.txt`, `pytest.ini`, `local.settings*.json`,
caches, and this README). What ships is exactly:

- `function_app.py` and `src/home_assistant_api/` -- runtime code
- `requirements.txt` -- production dependencies only
- `prompts/` -- the assistant system prompt asset
- `host.json` -- Functions host configuration

No test dependencies, no `pi-client/` or `infra/` files, and no generated
test artifacts are ever included.

## Design notes

- **Error handling**: every error raised is a specific `AppError` subclass
  from `errors.py` with a fixed `code`/`http_status`/`retryable`. The only
  bare `except Exception` in the codebase is the last-resort HTTP boundary
  safety net in `routes.with_error_handling`, which always logs via
  telemetry and returns a proper `ErrorResponse` -- it never fabricates a
  successful-looking result.
- **Testability**: `routes.py` handlers are plain functions of
  `(req, ctx, correlation_id)` callable directly with a hand-built
  `azure.functions.HttpRequest` and an `AppContext` populated with fakes --
  no Functions host required for tests. `AssistantOrchestrator` only
  requires a structurally-compatible `chat_client.chat.completions.create(...)`
  object, so tests never touch the real `openai` SDK network path.
- **Google adapters fail explicitly when unconfigured**: Google tools raise
  `ConfigurationError` (not an empty list or `None`) when a device has not
  completed OAuth or the backend has no OAuth client configured at all.
