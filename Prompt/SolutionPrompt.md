# Build Prompt: Jarvis Realtime Voice Assistant

Build the complete production solution described below. Continue until the
implementation, local validation, packaging, documentation, commit, and push
are complete. Do not leave placeholder code, TODOs, mocked production paths, or
partially migrated legacy features.

## 1. Product goal

Create a simple, low-latency, half-duplex voice assistant for a Raspberry Pi
3B:

1. The Pi listens locally for the wake phrase **"hey jarvis"**.
2. After detection, the Pi streams the user's command as raw PCM audio to an
   Azure Function.
3. The Function forwards audio to a Microsoft Foundry GPT Realtime deployment
   using Microsoft Entra authentication.
4. The Function streams generated PCM audio back to the Pi.
5. The Pi starts playback as soon as the first response bytes arrive.

Do not use push-to-talk. Do not support barge-in while the assistant is
speaking. Do not store recordings, transcripts, conversations, reminders, or
application state.

## 2. Non-negotiable simplicity rules

- Keep executable solution content under `_src/`.
- Keep only `README.md` and the `Prompt/` folder at repository root.
- Use one Pi process, one Azure Function App, one Function host Storage account,
  one Application Insights/Log Analytics pair, and one Foundry resource with
  one GPT Realtime deployment.
- Do not provision Azure AI Speech, Key Vault, application tables, Cosmos DB,
  Service Bus, API Management, Google services, or a device registry.
- Do not implement Google OAuth, calendar, Gmail, tasks, reminders, tools,
  device registration, persistent sessions, WAV/base64 JSON requests, API keys,
  connection-string authentication, or GitHub Actions.
- Keep the Pi release small and suitable for a Raspberry Pi 3B running 64-bit
  Raspberry Pi OS.

## 3. Voice state machine

Implement exactly these runtime states:

```text
IDLE_WAKEWORD
  -> ACTIVATED
  -> STREAMING_COMMAND
  -> WAITING_FOR_RESPONSE
  -> PLAYING_RESPONSE
  -> COOLDOWN
  -> IDLE_WAKEWORD
```

On errors, play the bundled offline/cancellation cue as appropriate, close all
audio/network resources, apply a short bounded retry delay, and return to
`IDLE_WAKEWORD`.

Wake-word processing must remain disabled from activation through playback and
for the configured cooldown so the assistant cannot trigger itself.

## 4. Raspberry Pi behavior

### 4.1 Wake word

- Support only `openWakeWord` using its TFLite/LiteRT inference path.
- Load only the bundled/pretrained `hey jarvis` model, never every model.
- Use 16 kHz, mono, signed 16-bit little-endian PCM.
- Feed the wake model efficient 80 ms frames.
- Default wake threshold: `0.5`, configurable from `config.env`.
- Do not include Porcupine, keyboard activation, or push-to-talk fallbacks.

### 4.2 Command capture and end-of-speech

- Reuse the active microphone stream across wake detection and command capture
  when practical, avoiding a gap that clips the first command syllable.
- Stream 20 ms PCM frames.
- Use WebRTC VAD locally, mode `2` by default.
- Cancel the turn if no command speech starts within `3.0` seconds.
- Once speech starts, end the request after `1.2` seconds of continuous
  non-speech.
- Enforce a **30.0-second hard maximum command duration**.
- Keep all audio in memory. Never create temporary audio files.

### 4.3 HTTP request and response

Send:

```http
POST /api/voice/stream
Content-Type: audio/pcm
X-Device-Guid: <configured UUID>
X-Audio-Sample-Rate: 16000
X-Audio-Channels: 1
X-Audio-Sample-Width: 2
Transfer-Encoding: chunked
```

- Use a generator/iterator as the request body so chunks are uploaded as they
  are captured.
- Do not retry a voice request after any body bytes have been sent; replaying
  partial speech can create duplicate answers.
- Use bounded connect and response timeouts.
- Read the response with streaming enabled and write each received PCM chunk to
  an already-open 24 kHz mono PCM16 output stream.
- Treat non-2xx responses, invalid response audio headers, timeouts, and broken
  streams as explicit errors.

### 4.4 Pi configuration

Keep only these operator settings:

- `HAP_API_BASE_URL`
- `HAP_DEVICE_GUID`
- `HAP_INPUT_DEVICE`
- `HAP_OUTPUT_DEVICE`
- `HAP_WAKEWORD_THRESHOLD`
- `HAP_VAD_MODE`
- `HAP_NO_SPEECH_TIMEOUT_SECONDS`
- `HAP_SILENCE_TIMEOUT_SECONDS`
- `HAP_MAX_COMMAND_SECONDS` (default and maximum `30.0`)
- `HAP_PLAYBACK_COOLDOWN_SECONDS`
- `HAP_LOG_LEVEL`

The installer must create a root-owned `0640`
`/etc/home-assistant-pi/config.env`, preserve it on reinstall, and require the
API URL and canonical UUID before starting the service.

## 5. Azure Function behavior

Use the Azure Functions Python v2 programming model and
`azurefunctions-extensions-http-fastapi`. All HTTP functions in the app must
use its FastAPI request/response types because Python HTTP streaming cannot be
mixed with the legacy HTTP model.

Keep only:

- `GET /api/health` -> `{"status":"ok"}`
- `POST /api/voice/stream` -> streaming PCM request and response

The voice route must:

1. Validate `X-Device-Guid` before connecting upstream.
2. Parse both configured and supplied values as canonical UUIDs and compare
   them with `secrets.compare_digest`.
3. Validate content type and fixed input audio headers.
4. Reject empty, odd-length, or oversized input. The server limit is exactly
   30 seconds of 16 kHz mono PCM16 (`960000` bytes).
5. Read `async for chunk in request.stream()` without buffering the complete
   request.
6. Incrementally resample input from 16 kHz to the Foundry-required 24 kHz on
   the Function, preserving resampler state between chunks.
7. Append base64-encoded 24 kHz PCM chunks to the Foundry Realtime input
   buffer as each HTTP chunk arrives.
8. Disable Foundry server VAD for this turn because end-of-speech is decided on
   the Pi.
9. After HTTP request EOF, commit the input buffer and create one audio
   response.
10. Return a `StreamingResponse` with raw 24 kHz mono PCM16 bytes and headers:
    `X-Audio-Sample-Rate: 24000`, `X-Audio-Channels: 1`, and
    `X-Audio-Sample-Width: 2`.
11. Wait for and decode the first valid `response.output_audio.delta` before
    returning `200`, so an upstream pre-stream failure can still return JSON.
12. Yield that chunk and each later audio delta immediately.
13. Stop on `response.done`; log and terminate on Foundry error events,
    disconnects, cancellation, or timeouts.
14. Close the Foundry connection, OpenAI client, and Azure credential in all
    paths.

The endpoint is turn-based, not full duplex: the Function processes upload
chunks before returning the streamed response. Do not claim WebSocket or
simultaneous bidirectional support from Azure Functions.

## 6. Foundry model and authentication

- Default model: `gpt-realtime-2`, model version `2026-05-06`.
- Default deployment name: `gpt-realtime-2`.
- Deployment SKU: `GlobalStandard`, pay-as-you-go.
- Permit an operator to override model name/version/deployment at provision
  time, for example to use `gpt-realtime-mini`.
- Configure the Realtime session for 24 kHz PCM input/output, one audio output
  modality, a concise home-assistant system instruction, and a configurable
  voice.
- Use `AsyncOpenAI` Realtime over
  `<endpoint converted to wss>/openai/v1`.
- Acquire tokens for `https://ai.azure.com/.default`.
- In Azure, use the Function App system-assigned managed identity only.
- Grant it `Cognitive Services OpenAI User` on the Foundry/OpenAI resource.
- Set `disableLocalAuth: true`; never list, output, store, or consume model
  keys.

## 7. Entra-only Azure infrastructure

Write idempotent Bicep that provisions:

- Resource group.
- Linux Flex Consumption Function App using Python 3.11.
- One always-ready HTTP instance to reduce cold-start latency.
- Maximum scale-out of 40, the Flex Consumption platform minimum.
- Minimal StorageV2 account and deployment blob container required by the
  Functions host.
- VNet with a `/27` subnet delegated to `Microsoft.App/environments` for Flex
  integration and a separate private-endpoint subnet.
- Blob, Queue, and Table Storage private endpoints, private DNS zones, and VNet
  links.
- Log Analytics workspace and Application Insights.
- Foundry/Azure OpenAI resource plus the configured Realtime model deployment.
- Required RBAC assignments.

Security requirements:

- Storage: `allowSharedKeyAccess: false`, `publicNetworkAccess: Disabled`, no
  connection strings, no application tables, and no public blobs.
- Route Function host and deployment Storage traffic through VNet integration
  and Blob/Queue/Table private endpoints; do not rely on Azure service bypass.
- Function host storage settings use identity-based
  `AzureWebJobsStorage__*ServiceUri` values.
- Foundry: `disableLocalAuth: true`.
- Monitoring: disable local ingestion authentication, set
  `APPLICATIONINSIGHTS_AUTHENTICATION_STRING=Authorization=AAD`, and grant the
  Function identity `Monitoring Metrics Publisher`. The Application Insights
  connection string may identify telemetry endpoints but must not authenticate
  ingestion.
- Function settings contain only endpoints, deployment/voice configuration,
  the fixed device UUID, and non-secret runtime flags.
- Enable `PYTHON_ENABLE_INIT_INDEXING=1`.
- Declare the language runtime only in `functionAppConfig.runtime`; do not add
  Flex-managed runtime or remote-build app settings such as
  `FUNCTIONS_WORKER_RUNTIME`, `FUNCTIONS_EXTENSION_VERSION`,
  `SCM_DO_BUILD_DURING_DEPLOYMENT`, or `ENABLE_ORYX_BUILD`.
- Disable FTP/SCM basic publishing authentication.
- Use TLS 1.2 or newer and HTTPS only.
- Do not output any key.

Model availability changes by region. Keep Function and Foundry locations
separate. Default the Function to `australiaeast` and the Foundry resource to a
currently supported nearby region such as `southindia`, while allowing both to
be overridden.

## 8. Idempotent lifecycle

The root README must contain exactly these primary sections:

1. Install application on Pi
2. Un install application on Pi
3. Install backend in Azure
4. UnInstall backend from azure

Azure install must:

- Check Azure CLI authentication and require Azure CLI 2.60.0 or newer.
- Register every required resource provider, including
  `Microsoft.AlertsManagement` for policy-created Application Insights smart
  detector alerts plus `Microsoft.Network` and `Microsoft.App` for private
  Storage connectivity, and verify each provider reaches `Registered`.
- Validate the selected Flex Consumption region and configured Foundry model,
  version, and deployment SKU before provisioning.
- Use one cross-platform Python lifecycle command; do not require azd.
- Expose an installer version and verify a versioned Bicep schema output before
  uploading backend code so a stale template cannot proceed silently.
- Generate one UUID with `uuid.uuid4()` only when the environment has none.
- Preserve that UUID in restricted local lifecycle state and recover it from an
  existing Function App when local state is unavailable.
- Provision/update resources; verify the Function identity, Storage RBAC, VNet
  integration, and approved private endpoints; deploy the Function; and verify
  `/api/health`.
- Print the API URL and UUID needed by the Pi.
- Accept separate `--location` and `--foundry-location` arguments.

Azure uninstall must:

- Require `--yes`.
- Delete the environment resource group and all contained services.
- Succeed when the group is already absent.
- Retain the local UUID while rotating the resource-name seed after deletion so
  soft-deleted Foundry names do not block reinstall.

Pi install/uninstall must:

- Be safe to rerun.
- Install only production dependencies and the one supported wake-word path.
- Require 64-bit Raspberry Pi OS.
- Run as the selected non-root desktop user with that user's PipeWire runtime
  environment; retain the runtime user for idempotent updates and enable linger
  so the service can start before interactive login.
- Clear persisted numeric PortAudio indexes when migrating between runtime
  users because device indexes are session-specific.
- Prefer a compatible configured/default PortAudio device and otherwise select
  the first device that supports the required mono PCM format; diagnostics must
  print the resolved microphone and speaker.
- Treat any systemd restart during the post-install stability window as an
  installation failure, stop the failed service, and print full service logs;
  rate-limit later runtime failures so missing hardware cannot loop forever.
- Preserve configuration unless uninstall uses `--purge-config`.
- Never depend on repository source after installing a release bundle.

Backend deployment must verify more than process availability: after the basic
health probe it must use the configured device identity to open, configure, and
close a Foundry Realtime WebSocket with the Function managed identity. The
Python runtime dependencies must include the async Azure Identity HTTP transport
(`aiohttp`) explicitly because it is not installed transitively by
`azure-identity`.

## 9. Repository cleanup

Delete every committed:

- `tests/` directory.
- `test_*.py` file.
- pytest configuration.
- test shell script or test helper.
- `requirements-dev.txt`.
- test-only dependency/optional extra.
- old Google, reminder, tool, Speech, persistence, token/device-registration,
  and WAV/base64 implementation.
- obsolete architecture/security documentation and old JSON schemas.

Do not add a committed replacement test suite.

## 10. Disposable validation policy

During implementation, create any tests and fixtures needed only under
`_src/.test-artifacts/`. Validate at least:

- Configuration bounds, canonical UUIDs, and constant-time authentication.
- VAD no-speech, long-pause, and exactly 30-second maximum behavior.
- Request chunking, odd-byte handling, byte limit, 16-to-24 kHz resampling, and
  response chunk decoding.
- Foundry session configuration and managed-identity token scope.
- Error/cancellation cleanup.
- Pi incremental playback and state transitions.
- Bicep build/lint plus explicit checks for `disableLocalAuth: true`,
  `allowSharedKeyAccess: false`, and absence of key settings/resources.
- Lifecycle dry-run behavior and release contents.
- Python syntax/import checks and production dependency installation.

Measure and report separately:

- Local/mocked end-of-speech to first-audio latency.
- Real Azure latency only when an actual deployment is available. Target
  `<=1.5 s p50` and `<=2.5 s p95`.
- Pi CPU/RSS and wake false-accept/reject rates only when an actual Pi 3B and
  representative recordings are available.

Before the final commit, delete `_src/.test-artifacts/`, all caches, build
metadata, temporary environments, recordings, and generated validation files.

## 11. Packaging and release

- Bump the Pi package to the next major version for this incompatible protocol.
- Build a wheel, source archive, and
  `home-assistant-pi-bundle-<version>.tar.gz`.
- Bundle only the wheel, runtime dependency metadata, config example,
  install/update/uninstall scripts, version, and release manifest. Generate the
  systemd unit during installation.
- Generate `SHA256SUMS`.
- Verify extraction, checksums, exact contents, and executable modes.
- Publish the Pi release if GitHub credentials are available so README download
  commands never point to a nonexistent artifact.

## 12. Completion criteria

The work is complete only when:

- The old architecture is fully removed.
- The 30-second limit is consistent in prompt, code, config, validation, and
  documentation.
- Both runtime projects install and import from clean environments.
- Bicep compiles successfully.
- Disposable validation passes and is then deleted.
- `git status` contains only intended source changes before commit.
- Changes are committed with a descriptive message and pushed directly to
  `main` without creating a pull request.
