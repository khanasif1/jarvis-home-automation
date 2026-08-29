# Architecture

## Runtime path

```mermaid
flowchart LR
    Mic[Pi microphone] --> Buffer[Persistent PortAudio callback<br/>bounded 20 ms frames]
    Buffer -->|raw 80 ms frames| Wake[openWakeWord ensemble<br/>Jarvis]
    Wake --> Greet[How can I help?]
    Greet --> Enhance[SpeexDSP<br/>denoise + bounded AGC]
    Buffer --> Enhance
    Enhance --> VAD[WebRTC VAD mode 1<br/>20 ms frames]
    VAD -->|first query| Fn[Azure Function<br/>HTTP streaming]
    VAD -->|follow-up audio| Intent[Foundry intent<br/>JARVIS_QUERY or JARVIS_SLEEP]
    Intent -->|JARVIS_QUERY| Fn
    Fn -->|incremental resample| RT[Microsoft Foundry<br/>GPT Realtime]
    RT -->|24 kHz PCM deltas| Fn
    Fn -->|streamed 24 kHz PCM| Speaker[Pi speaker]
    Speaker --> Follow[Anything else?]
    Follow -->|160 ms speech within 30 seconds| VAD
    Intent -->|JARVIS_SLEEP| Sleep[Local sleep prompt]
    Follow -->|30-second silence| Sleep
    Sleep -->|reset, pre-warm, immediate capture| Wake
```

The Pi owns only physical audio, wake detection, local session prompts,
follow-up timing, turn termination, and playback. The Function owns protocol
validation, authentication, resampling, Microsoft Entra token acquisition, and
the Realtime session. GPT Realtime owns speech understanding, response
generation, and speech synthesis in one model session.

## Pi state machine

```text
IDLE_WAKEWORD
  -> ACTIVATED
  -> STREAMING_COMMAND
  -> WAITING_FOR_RESPONSE
  -> PLAYING_RESPONSE
  -> STREAMING_COMMAND (follow-up)
  -> COOLDOWN
  -> IDLE_WAKEWORD
```

- `IDLE_WAKEWORD`: two pre-warmed TFLite models consume raw 80 ms microphone
  frames from the persistent callback buffer.
- `ACTIVATED`: play the local spoken greeting.
- `STREAMING_COMMAND`: enhance commands with SpeexDSP, require 160 ms of
  VAD-positive input while tolerating gaps up to 40 ms, then retain only bounded
  command audio plus a short pre-roll. Follow-up audio is classified before an
  answer is requested.
- `WAITING_FOR_RESPONSE`: dispatch the chunked request in a worker and wait
  silently for the first response audio.
- `PLAYING_RESPONSE`: write response chunks directly to PortAudio, ask for
  another query, and loop when follow-up speech begins.
- `COOLDOWN`: reset and pre-warm the detector after the sleep prompt. The
  default delay is zero, so microphone wake capture reopens immediately.

The design is half-duplex. No barge-in or wake-word inference runs during a
turn. It intentionally omits acoustic echo cancellation because playback and
capture do not run concurrently and therefore cannot provide a synchronized
speaker-reference stream.

## Turn completion and limits

WebRTC VAD cancels an initial activation if 160 ms of confirmed speech does not
begin within 3 seconds and closes a follow-up wait after 30 seconds. Up to 40 ms
of non-speech may occur inside the speech-start candidate, but short/noise-only
candidates still fail. The long pre-speech wait is not uploaded. After speech
starts, 1.2 seconds of silence closes the request. A follow-up classifier maps
explicit termination and unclear/noise-only input to `JARVIS_SLEEP`; it maps
only a clear new request to `JARVIS_QUERY`. Regardless of VAD, 30 seconds
(`960,000` input bytes) is a hard command ceiling enforced independently on the
Pi and Function.

Azure Functions supports streamed HTTP bodies and responses but is not a
full-duplex WebSocket host. The Pi dispatches the bounded in-memory command as a
chunked body after local VAD closes it. Foundry input is committed at request
EOF. Response headers are returned only after the first valid Foundry audio
delta, then later deltas stream directly to the Pi. Request setup is
intentionally silent so only the concise activation and follow-up prompts are
spoken.

## Audio contract

| Direction | Encoding | Frame/chunk strategy |
|---|---|---|
| Microphone to wake ensemble | PCM16 LE, mono, 16 kHz | raw 80 ms / 2,560-byte inference frames |
| Pi to Function | Speex-enhanced PCM16 LE, mono, 16 kHz | 20 ms / 640-byte capture frames |
| Function to Foundry | PCM16 LE, mono, 24 kHz | stateful incremental resampling |
| Function to Pi | PCM16 LE, mono, 24 kHz | streamed model audio deltas |

No WAV container, base64 HTTP body, conversation store, or temporary audio file
exists in the application path. Base64 is used only inside the Foundry
WebSocket protocol.

The input callback remains active across wake, prompts, commands, and sleep
re-arm, but queues audio only for the current listening phase. This removes
PortAudio reopen gaps while preventing stale prompt/playback audio from entering
the next phase. A bounded two-second queue drops oldest frames rather than
blocking the real-time callback, and each turn reports overflow/drop and
RMS/peak/clipping metrics without logging audio content.

## Azure resources

The Function runs on Flex Consumption with one always-ready HTTP instance to
avoid normal cold-start latency. Its system-assigned identity accesses the
Functions host Storage account and Foundry. The Function is VNet-integrated;
Blob, Queue, and Table Storage resolve through private endpoints and private
DNS, while Storage public network access and shared-key access are disabled.
Monitoring uses Application Insights and Log Analytics. There are no Google,
Speech, Key Vault, or application-database resources.
