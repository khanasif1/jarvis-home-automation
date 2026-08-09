**Recommended approach:** keep wake-word detection and audio capture/playback on the Raspberry Pi, but perform STT, AI reasoning, tool execution, and TTS in Azure. Start with a turn-based HTTPS API because it is simpler and more reliable than Voice Live streaming. Add Voice Live as a later upgrade, routed through your backend so Azure credentials never live on the Pi.

Paste this prompt into GitHub Copilot:

```text
Act as a senior Azure cloud architect, Python engineer, IoT engineer, and security engineer. Build a complete, deployable MVP for a voice-enabled home assistant running on a Raspberry Pi 3B.

Do not provide only a tutorial or pseudocode. Create the actual repository, source files, infrastructure-as-code, automated tests, deployment scripts, configuration examples, and documentation. Work incrementally, run the available tests after each meaningful phase, and fix failures before continuing.

# 1. Product objective

Create a voice-enabled home assistant with these capabilities:

1. Run continuously on a Raspberry Pi 3B connected to Wi-Fi.
2. Use a USB or 3.5 mm microphone for input.
3. Use an attached speaker for output.
4. Detect a configurable local wake word such as:
   - "Jarvis"
   - "Wake up"
5. Record the user's request only after the wake word is detected.
6. Send the recorded audio securely to an Azure backend.
7. Convert speech to text in Azure.
8. Send the text to an Azure OpenAI model.
9. Allow the model to use strongly typed tools to:
   - Add items to a to-do list.
   - List to-do items.
   - Complete to-do items.
   - Remove to-do items.
   - Create reminders.
   - List reminders.
   - Cancel reminders.
10. Convert the assistant's final response to speech in Azure.
11. Return the generated audio to the Raspberry Pi.
12. Play the response through the Pi's speaker.
13. Deliver reminders through the speaker when they become due.
14. Optionally integrate with:
   - Google Calendar.
   - Google Tasks.
   - Gmail read-only search and summaries.
15. Run the Pi application automatically after startup using systemd.

The initial application is single-user and intended for a private home network, but its design must not prevent future support for multiple users or multiple devices.

Default user timezone: Australia/Sydney.

Never hardcode the current date. Inject the current UTC time and the user's timezone into every AI request at runtime.

# 2. Architecture decision

Implement the first release as a turn-based voice application.

Use this flow:

Raspberry Pi
  -> local wake-word detection
  -> local voice activity detection
  -> record PCM WAV audio
  -> HTTPS request to Azure backend
  -> Azure Speech-to-Text
  -> Azure OpenAI with function/tool calling
  -> backend executes the selected tool
  -> Azure Text-to-Speech
  -> backend returns JSON containing response metadata and Base64 audio
  -> Raspberry Pi decodes and plays the WAV response

Do not perform full speech-to-text or text-to-speech locally on the Raspberry Pi in the MVP. The Pi 3B has limited CPU and memory, and cloud speech services should provide better accuracy and voices.

Do not connect the Pi directly to Azure Voice Live using a permanent Azure API key. Permanent Azure credentials must never be stored on the Pi.

Do not use Logic Apps in the real-time voice path. Logic Apps may later be used for non-real-time workflows, but they are not appropriate for low-latency audio turns.

Use Azure Functions with the Python v2 programming model for the MVP backend. This is appropriate because the MVP uses short, turn-based HTTPS calls rather than long-lived WebSocket connections.

Design the business logic so that a future Voice Live streaming service can reuse:

- The to-do repository.
- The reminder repository.
- Google integrations.
- AI tool implementations.
- Authentication.
- Logging.
- Assistant system prompt.

Document a phase-two architecture using Azure App Service or Azure Container Apps for WebSocket streaming to Azure Voice Live, but do not make Voice Live necessary for the MVP.

# 3. Technology stack

Use the following unless a compatibility problem is found:

## Raspberry Pi client

- Raspberry Pi OS Bookworm.
- Python 3.11 or the supported system Python.
- asyncio where useful.
- requests or httpx for HTTPS calls.
- pvporcupine for efficient wake-word detection when configured.
- A WakeWordDetector interface so other engines can be substituted.
- A keyboard or push-to-talk detector for development without Porcupine.
- Optionally support openWakeWord behind a configuration flag.
- webrtcvad or a compatible ARM-supported VAD package.
- sounddevice/PortAudio or ALSA arecord/aplay.
- wave for WAV encoding.
- pydantic-settings for configuration.
- tenacity or an equivalent bounded retry mechanism.
- structlog or standard structured logging.

Prefer ALSA-compatible, dependable approaches over desktop-specific audio libraries.

## Azure backend

- Azure Functions Python v2.
- Azure AI Speech for speech-to-text and text-to-speech.
- Azure OpenAI using the currently supported stable Python SDK.
- Azure Table Storage for:
  - To-do items.
  - Reminders.
  - Device records.
  - Conversation state.
  - Idempotency records.
- Azure Key Vault for secrets.
- Application Insights for observability.
- Managed Identity and DefaultAzureCredential wherever supported.
- pytest for tests.
- Bicep and Azure Developer CLI configuration for deployment.

If Azure Speech cannot use Managed Identity through the selected stable SDK, store its key in Key Vault and expose it to the Function App through a secure Key Vault reference. Never place the key in source control.

## Google integration

- Google OAuth 2.0 authorization-code flow with PKCE.
- Least-privilege scopes.
- Google Calendar API.
- Google Tasks API.
- Gmail API with read-only scope only.
- Feature flags so the core assistant works without Google configuration.

# 4. Repository structure

Create a monorepo similar to:

home-assistant/
  README.md
  .gitignore
  .env.example
  azure.yaml
  pyproject.toml
  docs/
    architecture.md
    security.md
    raspberry-pi-setup.md
    azure-deployment.md
    google-oauth-setup.md
    troubleshooting.md
    voice-live-phase-two.md
  prompts/
    assistant_system.txt
  pi-client/
    pyproject.toml
    src/
      home_assistant_pi/
        __init__.py
        main.py
        config.py
        audio/
          __init__.py
          capture.py
          playback.py
          vad.py
          wav.py
        wakeword/
          __init__.py
          base.py
          porcupine.py
          keyboard.py
          openwakeword.py
        api/
          __init__.py
          client.py
          models.py
        reminders/
          __init__.py
          poller.py
        state_machine.py
        logging_config.py
    tests/
    scripts/
      test_microphone.py
      test_speaker.py
      list_audio_devices.py
      run_push_to_talk.py
    systemd/
      home-assistant.service
  backend/
    function_app.py
    host.json
    requirements.txt
    src/
      home_assistant_api/
        __init__.py
        config.py
        auth.py
        errors.py
        models.py
        speech/
          stt.py
          tts.py
        ai/
          orchestrator.py
          prompt.py
          tool_definitions.py
          tool_executor.py
        tools/
          todos.py
          reminders.py
          google_calendar.py
          google_tasks.py
          gmail.py
        repositories/
          todos.py
          reminders.py
          sessions.py
          devices.py
          idempotency.py
        google/
          oauth.py
          credentials.py
          calendar_client.py
          tasks_client.py
          gmail_client.py
        telemetry.py
        time_utils.py
    tests/
      unit/
      integration/
  infra/
    main.bicep
    main.parameters.json
    modules/
      function-app.bicep
      storage.bicep
      key-vault.bicep
      monitoring.bicep
      speech.bicep
      openai.bicep
      role-assignments.bicep
  scripts/
    bootstrap.ps1
    bootstrap.sh
    deploy.ps1
    deploy.sh

Adjust the structure only when required by Azure Functions conventions. Keep domain logic outside function_app.py so it can be tested independently.

# 5. Raspberry Pi behavior

Implement the Pi client as an explicit state machine:

IDLE
  -> WAKE_WORD_DETECTED
  -> LISTENING
  -> PROCESSING
  -> SPEAKING
  -> IDLE

Also support ERROR and SHUTTING_DOWN states.

Requirements:

1. While IDLE:
   - Continuously monitor microphone frames locally.
   - Do not upload audio.
   - Detect the configured wake word.
   - Use low CPU where possible.

2. When the wake word is detected:
   - Play a short activation tone.
   - Stop feeding audio to the wake-word engine.
   - Begin recording the command.
   - Keep approximately 300 ms of pre-roll audio where practical.

3. Recording:
   - PCM signed 16-bit little-endian.
   - Mono.
   - 16 kHz unless the selected Azure STT configuration requires another rate.
   - Use 20 ms or 30 ms frames compatible with VAD.
   - Stop after approximately 900 ms of silence following detected speech.
   - Require a configurable minimum speech duration.
   - Enforce a default maximum recording duration of 15 seconds.
   - If no speech is detected, return to IDLE with a short cancellation tone.
   - Write temporary audio only when needed and delete it after processing.
   - Never retain raw recordings by default.

4. Processing:
   - Generate a UUID request ID.
   - Generate or reuse a conversation session ID.
   - Send the WAV body to the backend.
   - Use bounded retries only for transient errors.
   - Do not automatically retry a request that could duplicate an action unless the same request ID is reused.
   - Display useful logs without logging raw audio, authentication tokens, or private message content.

5. Speaking:
   - Decode returned Base64 WAV audio.
   - Play it through the configured ALSA output.
   - Do not listen for normal commands during playback in the MVP, to avoid the assistant hearing itself.
   - Return to IDLE after playback.
   - Keep the design open for phase-two barge-in support.

6. Offline behavior:
   - Play a bundled local sound or prerecorded generic message when the backend is unreachable.
   - Do not pretend that a task or reminder was created.
   - Retry network connection on the next interaction.
   - Continue running rather than crashing.

7. Development behavior:
   - Provide push-to-talk or keyboard activation.
   - Allow a text-only backend request in development mode.
   - Make hardware-specific dependencies injectable and mockable.

8. Reminder behavior:
   - Poll the backend every 10 seconds by default.
   - Poll only while the Pi has network connectivity.
   - Retrieve reminders assigned to the device that are due and not acknowledged.
   - Request or receive synthesized reminder audio.
   - Play a notification sound and then speak the reminder.
   - Acknowledge the reminder only after successful playback.
   - Avoid duplicate playback after restart by using reminder delivery IDs and acknowledgement state.

# 6. Backend HTTP API

All endpoints must be versioned under /api/v1.

Return stable error objects with:

{
  "error": {
    "code": "stable_machine_readable_code",
    "message": "Safe user-readable message",
    "retryable": false,
    "request_id": "uuid"
  }
}

## POST /api/v1/voice/turn

Request:

- Content-Type: audio/wav
- Body: binary WAV data.
- Authorization: Bearer <device token>
- X-Request-ID: UUID
- X-Device-ID: configured device ID
- X-Session-ID: UUID
- X-Timezone: Australia/Sydney
- X-Client-Version: semantic version

Validate:

- Authentication.
- Device ID.
- Content type.
- Audio size.
- WAV encoding.
- Maximum duration.
- Request ID.
- Timezone.
- Rate limits.

Response:

{
  "request_id": "uuid",
  "session_id": "uuid",
  "transcript": "Add milk and eggs to my grocery list",
  "assistant_text": "I added milk and eggs to your grocery list.",
  "audio_base64": "...",
  "audio_content_type": "audio/wav",
  "actions": [
    {
      "type": "todo_created",
      "id": "uuid",
      "summary": "milk"
    },
    {
      "type": "todo_created",
      "id": "uuid",
      "summary": "eggs"
    }
  ],
  "requires_clarification": false,
  "server_time_utc": "ISO-8601 timestamp"
}

The endpoint must:

1. Check the idempotency table using device ID and request ID.
2. Return the original result for a repeated completed request.
3. Convert audio to text.
4. Load bounded conversation context.
5. Inject current UTC time and the user's timezone.
6. Invoke Azure OpenAI with tool definitions.
7. Execute requested tools.
8. Return tool results to the model.
9. Repeat until a final assistant response is produced.
10. Limit tool iterations to prevent infinite loops.
11. Synthesize the final assistant response.
12. Save only the necessary bounded conversation state.
13. Never store raw audio by default.
14. Save an idempotent response or action record.
15. Return the result.

## GET /api/v1/reminders/due

Parameters:

- device_id
- optional limit, default 10

Return reminders where:

- due_at_utc is less than or equal to current time.
- status is scheduled or retryable.
- the reminder belongs to the authenticated user/device.
- the reminder has not been acknowledged.

## POST /api/v1/reminders/{reminder_id}/ack

Body:

{
  "delivery_id": "uuid",
  "played_at_utc": "ISO-8601 timestamp"
}

The acknowledgement must be idempotent.

## To-do management endpoints

Implement authenticated endpoints for testing and future UI use:

- GET /api/v1/todos
- POST /api/v1/todos
- PATCH /api/v1/todos/{todo_id}
- DELETE /api/v1/todos/{todo_id}

## Reminder management endpoints

- GET /api/v1/reminders
- POST /api/v1/reminders
- DELETE /api/v1/reminders/{reminder_id}

## Health endpoints

- GET /api/v1/health/live
- GET /api/v1/health/ready

The readiness endpoint may check configuration and dependent service reachability, but must not expose secrets.

## Development-only endpoint

Allow a text-turn endpoint only when APP_ENV=development:

POST /api/v1/debug/text-turn

This endpoint must reuse the same AI orchestration and tool execution logic as voice requests. It must not be deployed or enabled in production.

# 7. AI orchestration and tool calling

Do not let the model directly modify storage or call Google APIs. It may only request validated backend tools.

Use explicit JSON schemas for tools.

Implement at least these tools:

## add_todo

Input:

{
  "items": ["milk", "eggs"],
  "list_name": "groceries"
}

Rules:

- Trim and validate items.
- Reject empty items.
- Avoid accidental duplicate items when the same request is retried.
- Do not silently merge unrelated items.
- Return created IDs and normalized text.

## list_todos

Input:

{
  "list_name": "groceries",
  "status": "open"
}

## complete_todo

Input:

{
  "todo_id": null,
  "item_text": "buy milk",
  "list_name": "groceries"
}

If more than one item matches, return an ambiguity result and have the assistant ask the user which one.

## remove_todo

Use the same matching and ambiguity behavior as complete_todo.

## create_reminder

Input:

{
  "title": "take the bins out",
  "due_at_local": "2026-08-10T19:00:00",
  "timezone": "Australia/Sydney",
  "recurrence": null
}

The model will suggest a date, but backend code must validate it.

Rules:

- Convert local times to UTC using zoneinfo.
- Retain the original IANA timezone.
- Reject invalid timezones.
- Detect past dates.
- Handle daylight-saving transitions.
- Ask for clarification when the date or time is ambiguous.
- Do not guess a time when the user has not supplied enough information.
- Support null recurrence in the MVP.
- If recurrence is implemented, use a validated structured recurrence model rather than free text.

## list_reminders

Input:

{
  "start_at_local": null,
  "end_at_local": null,
  "status": "scheduled"
}

## cancel_reminder

Input:

{
  "reminder_id": null,
  "title": "take the bins out"
}

Ambiguous matches require clarification.

## Google tools

Behind feature flags, implement:

- create_google_calendar_event
- list_google_calendar_events
- create_google_task
- list_google_tasks
- search_gmail

Gmail must remain read-only. Do not implement sending, deleting, moving, or marking Gmail messages in the MVP.

Do not call Gmail unless the user explicitly asks a mail-related question.

For destructive Google Calendar operations, require explicit confirmation before execution.

# 8. Assistant system prompt

Create prompts/assistant_system.txt containing a production-quality prompt similar to the following, refined as needed:

You are a private, voice-enabled home assistant.

Your primary responsibilities are managing the user's to-do lists and reminders. Optional Google tools may be available.

The current UTC time, user's local time, timezone, device ID, and available tools are supplied separately for each request.

Rules:

1. Keep spoken responses concise and natural, normally one or two short sentences.
2. Use tools for actions. Never claim an item, reminder, task, or event was created, changed, or deleted until the tool confirms success.
3. When the user gives several grocery or to-do items in one request, preserve them as separate items unless the user clearly describes a single combined task.
4. Ask a concise clarification question when:
   - A reminder has no usable date or time.
   - A date is ambiguous.
   - Multiple items match a completion or deletion request.
   - A requested action is unclear.
5. Confirm completed actions by stating what changed.
6. Do not expose internal tool names, JSON, IDs, credentials, or implementation details.
7. Never invent calendar events, emails, tasks, or reminder results.
8. Treat tool errors as failures and explain them briefly.
9. Require confirmation before:
   - Deleting multiple items.
   - Clearing a list.
   - Cancelling multiple reminders.
   - Deleting or changing calendar events.
10. Gmail access is read-only and may be used only when the user explicitly asks about email.
11. Do not send email or messages.
12. Do not reveal private email or calendar content unless needed to answer the user's explicit request.
13. If the user asks for emergency, medical, legal, or safety-critical assistance, state the limitations and direct them to an appropriate human or emergency service.
14. If an action cannot be performed, say so clearly rather than implying success.
15. Format responses for speech. Avoid Markdown, tables, URLs, long lists, and technical notation unless specifically requested.

Do not hardcode timestamps in this prompt. Append runtime context separately.

# 9. Storage models

Use UTC timestamps internally and UUIDs for IDs.

## Todo

- partition_key/user_id
- id
- list_name
- text
- normalized_text
- status: open or completed
- created_at_utc
- updated_at_utc
- completed_at_utc
- source: voice, API, Google
- source_request_id
- external_provider
- external_id
- version/etag

## Reminder

- partition_key/user_id
- id
- device_id
- title
- due_at_utc
- original_local_datetime
- timezone
- recurrence
- status: scheduled, due, acknowledged, cancelled, failed
- created_at_utc
- updated_at_utc
- acknowledged_at_utc
- source_request_id
- delivery_id
- delivery_attempts
- last_delivery_at_utc
- external_calendar_event_id
- version/etag

## Device

- device_id
- user_id
- display_name
- enabled
- timezone
- token_hash
- token_created_at_utc
- last_seen_at_utc
- client_version

Do not store a plaintext device token.

## Conversation session

- user_id
- session_id
- device_id
- bounded message history
- created_at_utc
- updated_at_utc

Keep only the most recent configurable number of turns, default 10. Provide a configuration option to disable conversation persistence.

## Idempotency record

- device_id
- request_id
- status
- response payload or response reference
- created_at_utc
- expires_at_utc

# 10. Authentication and security

Implement the following:

1. HTTPS only.
2. Per-device bearer token for the MVP.
3. Store only a salted secure hash of the device token.
4. Use constant-time comparisons.
5. Include a provisioning script that generates a strong random token and creates the device record.
6. Store the token on the Pi in a root-readable configuration file with mode 600.
7. Support token rotation.
8. Never log:
   - Device tokens.
   - Azure keys.
   - Google tokens.
   - Raw audio.
   - Full Gmail bodies.
9. Redact Authorization headers and secrets.
10. Validate request body sizes.
11. Limit audio duration.
12. Apply per-device rate limiting.
13. Use Managed Identity for Azure resources where possible.
14. Use Key Vault for remaining secrets.
15. Assign least-privilege Azure roles.
16. Configure CORS as disabled or restrictive because the Pi is not a browser client.
17. Do not return stack traces in production.
18. Add dependency version pinning.
19. Add secret scanning guidance.
20. Do not commit .env files, tokens, audio recordings, OAuth files, or generated credentials.
21. Raw user audio must not be stored unless an explicit diagnostic setting is enabled.
22. Diagnostic audio retention must be off by default and clearly warn about privacy when enabled.

For a future direct Voice Live connection, document that the backend must issue a short-lived token or proxy the connection. Never recommend embedding a permanent Azure key in the Raspberry Pi image.

# 11. Google integration

Google integration must be optional and disabled by default.

Implement an interface so local Azure to-do/reminder storage remains the default provider.

Use the minimum scopes required:

- Google Tasks scope only when Google Tasks is enabled.
- Calendar event scope rather than full Calendar access where practical.
- Gmail read-only scope only when Gmail search is enabled.

OAuth requirements:

- Authorization-code flow with PKCE.
- Validate OAuth state.
- Request offline access only when needed.
- Encrypt or securely store refresh tokens.
- For this single-user MVP, a Google refresh token may be stored as a Key Vault secret.
- Never return Google tokens to the Pi.
- Provide setup documentation for creating the Google Cloud project and OAuth consent configuration.
- Provide an OAuth status endpoint that reveals connection state but not credentials.
- Allow the user to revoke the integration.
- Handle expired or revoked consent explicitly.

Google failures must not break local Azure-based to-do and reminder functionality.

# 12. Configuration

Use environment variables and typed settings.

Include .env.example files with names but no real values.

Pi settings should include:

- HOME_ASSISTANT_API_URL
- HOME_ASSISTANT_DEVICE_ID
- HOME_ASSISTANT_DEVICE_TOKEN_FILE
- HOME_ASSISTANT_TIMEZONE
- WAKEWORD_PROVIDER
- WAKEWORD_KEYWORD
- PORCUPINE_ACCESS_KEY
- PORCUPINE_KEYWORD_PATH
- AUDIO_INPUT_DEVICE
- AUDIO_OUTPUT_DEVICE
- AUDIO_SAMPLE_RATE
- AUDIO_MAX_RECORD_SECONDS
- VAD_SILENCE_MS
- REMINDER_POLL_SECONDS
- LOG_LEVEL

Backend settings should include:

- APP_ENV
- AZURE_STORAGE_ACCOUNT_URL
- AZURE_KEY_VAULT_URL
- AZURE_OPENAI_ENDPOINT
- AZURE_OPENAI_DEPLOYMENT
- AZURE_OPENAI_API_VERSION if required
- AZURE_SPEECH_ENDPOINT or region
- AZURE_SPEECH_VOICE
- AZURE_SPEECH_LANGUAGE
- CONVERSATION_HISTORY_LIMIT
- GOOGLE_INTEGRATION_ENABLED
- GOOGLE_CALENDAR_ENABLED
- GOOGLE_TASKS_ENABLED
- GMAIL_READONLY_ENABLED
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET_NAME
- GOOGLE_REDIRECT_URI
- DEFAULT_TIMEZONE
- MAX_AUDIO_BYTES
- MAX_TOOL_ITERATIONS
- LOG_LEVEL

Validate required settings during startup and return a clear readiness failure rather than silently using insecure defaults.

# 13. Reliability requirements

1. Every mutating voice request must be idempotent.
2. Use optimistic concurrency/ETags for storage updates where appropriate.
3. Use timeouts on every external call.
4. Retry only transient Azure or Google errors.
5. Use exponential backoff with jitter and a strict maximum.
6. Do not broadly catch and ignore exceptions.
7. Preserve stable error codes.
8. Separate user-safe error messages from internal diagnostics.
9. Use correlation IDs across:
   - Pi logs.
   - Function logs.
   - Speech calls.
   - Azure OpenAI calls.
   - Tool calls.
10. Ensure one failed tool cannot be reported as successful.
11. Limit model tool-call iterations, default 4.
12. Validate all model-produced arguments with Pydantic before execution.
13. Treat model output as untrusted input.
14. Never execute arbitrary code, shell commands, URLs, SQL, or dynamic imports requested by the model.

# 14. Observability

Use structured logging and Application Insights.

Capture:

- Request ID.
- Device ID.
- Session ID.
- Operation.
- Duration.
- STT duration.
- Model duration.
- Tool duration.
- TTS duration.
- Response status.
- Safe error code.
- Retry count.

Do not log private transcripts by default. Allow transcript logging only through an explicit development setting.

Add basic metrics for:

- Voice-turn success rate.
- End-to-end latency.
- STT failures.
- Tool failures.
- TTS failures.
- Due reminder delivery.
- Duplicate/idempotent requests.
- Authentication failures.

# 15. Infrastructure as code

Create Bicep modules and azd configuration capable of provisioning:

- Storage account.
- Required Azure Tables.
- Function App.
- Function hosting resources.
- Key Vault.
- Application Insights.
- Log Analytics where required.
- User-assigned or system-assigned Managed Identity.
- Role assignments.
- Azure AI Speech resource.
- Azure OpenAI resource or support parameters for an existing resource.
- Function application settings.
- Outputs required for configuring the Pi.

Do not put secrets in Bicep parameter files.

Where model deployment automation is unreliable or varies by region, make the Azure OpenAI endpoint and deployment name parameters and document the manual prerequisite.

Generate deployment commands using azd where possible:

azd auth login
azd env new
azd up

Also document equivalent Azure CLI prerequisites.

# 16. Raspberry Pi installation

Provide exact setup documentation covering:

1. Install Raspberry Pi OS.
2. Connect Wi-Fi.
3. Update packages.
4. Install:
   - Python dependencies.
   - PortAudio/ALSA dependencies.
   - libasound development packages where needed.
5. List audio devices.
6. Test microphone recording.
7. Test speaker playback.
8. Configure default ALSA input and output.
9. Create a Python virtual environment.
10. Install the Pi package.
11. Create the secure device-token file.
12. Configure environment settings.
13. Run push-to-talk mode.
14. Test wake-word mode.
15. Install and enable the systemd service.
16. Inspect logs with journalctl.
17. Restart and confirm automatic startup.

The systemd unit must:

- Run under a dedicated non-root user.
- Start after network-online.target and sound.target.
- Restart on failure with a bounded delay.
- Use an EnvironmentFile.
- Use the virtual environment's Python executable.
- Avoid exposing secrets in command-line arguments.
- Apply reasonable service hardening without blocking audio access.

# 17. Automated testing

Add meaningful tests, not placeholder assertions.

## Pi unit tests

Test:

- State transitions.
- Wake-word event handling.
- VAD stop conditions.
- Maximum recording duration.
- WAV creation.
- API timeout behavior.
- Retry behavior.
- Base64 response decoding.
- Reminder polling and acknowledgement.
- Duplicate reminder suppression.
- Configuration validation.

Use fake microphone, speaker, wake-word detector, clock, and API client implementations.

## Backend unit tests

Test:

- Device authentication.
- Invalid token rejection.
- Audio validation.
- STT service abstraction.
- AI tool argument validation.
- Maximum tool iterations.
- To-do creation.
- Multi-item grocery creation.
- Completing and deleting to-dos.
- Ambiguous item matching.
- Reminder timezone conversion.
- Daylight-saving boundaries for Australia/Sydney.
- Past reminder rejection.
- Missing reminder time clarification.
- Idempotent voice requests.
- Idempotent reminder acknowledgement.
- Conversation history bounds.
- Google-disabled behavior.
- Log redaction.
- Error response format.

## Integration tests

Use Azurite where practical and mock Azure Speech, Azure OpenAI, and Google APIs.

Test complete flows such as:

1. "Add milk and eggs to my grocery list."
2. "What is on my grocery list?"
3. "Mark milk as complete."
4. "Remind me tomorrow at 7 PM to take the bins out."
5. Poll due reminders and acknowledge successful playback.
6. Retry the same voice request ID and confirm no duplicate item is created.
7. Simulate an Azure OpenAI timeout and confirm no false success response.
8. Simulate TTS failure after a successful tool action and ensure retrying the same request does not duplicate the action.

Add a GitHub Actions workflow to run formatting, linting, type checks, and tests using tools already included in the project.

# 18. Acceptance criteria

The MVP is complete only when:

1. The Pi application starts and reaches IDLE.
2. Push-to-talk mode works without a wake-word provider key.
3. Wake-word mode works when Porcupine is configured.
4. Audio is captured in a format accepted by the backend.
5. The backend authenticates the device.
6. A spoken grocery request creates separate to-do items.
7. The assistant speaks a confirmation.
8. Listing and completing to-dos works.
9. Creating, listing, and cancelling reminders works.
10. A due reminder is spoken once and acknowledged.
11. Retrying an identical request does not duplicate actions.
12. The backend never stores raw audio by default.
13. No Azure or Google credentials are present in the repository or Pi source files.
14. The solution can be deployed using documented commands.
15. The Pi client can be installed as a systemd service.
16. Unit and integration tests pass.
17. The README contains a clear quick-start path.
18. Failure modes do not result in the assistant falsely claiming success.

# 19. Phase-two Voice Live design

After the MVP is complete, add docs/voice-live-phase-two.md.

Describe:

- Moving the streaming endpoint to Azure App Service or Azure Container Apps.
- Using secure WSS connections.
- Streaming microphone PCM frames instead of uploading a completed WAV.
- Connecting the backend to Azure Voice Live.
- Reusing the existing tool executor and repositories.
- Streaming generated audio back to the Pi.
- Interruption and barge-in.
- Echo cancellation.
- Connection recovery.
- Short-lived device/session authentication.
- Why Azure Functions is not used for the long-lived streaming connection.
- An optional token-broker design if the Pi ever connects directly to Voice Live.
- Why permanent Azure keys must never be provisioned to the Pi.

Do not replace the working turn-based MVP with an incomplete streaming implementation.

# 20. Implementation workflow

Follow this order:

1. Create the repository structure and dependency definitions.
2. Implement shared models and interfaces.
3. Implement backend storage repositories.
4. Implement to-do and reminder tools.
5. Implement the AI orchestration layer with mocked model tests.
6. Implement Speech-to-Text and Text-to-Speech adapters.
7. Implement the HTTP endpoints.
8. Implement backend authentication and idempotency.
9. Implement the push-to-talk Pi client.
10. Implement audio capture, VAD, and playback.
11. Implement wake-word providers.
12. Implement reminder polling and acknowledgement.
13. Add systemd configuration.
14. Add optional Google providers.
15. Add Bicep and azd deployment.
16. Run all automated tests.
17. Add hardware test scripts and documentation.
18. Review the complete solution for secrets, privacy, error handling, and false-success paths.

When an external service cannot be exercised without credentials, implement and test it through an interface and mock, then clearly identify the manual configuration needed.

At the end, provide:

- A concise architecture summary.
- The created file tree.
- Local development commands.
- Azure deployment commands.
- Raspberry Pi installation commands.
- Required manual Azure and Google configuration.
- Test results.
- Any remaining hardware-dependent validation steps.

Do not leave core behavior as TODO comments. Optional phase-two Voice Live functionality may be documented or scaffolded, but the turn-based to-do and reminder MVP must be complete and runnable.
```