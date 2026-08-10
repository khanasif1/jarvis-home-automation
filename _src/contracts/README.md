# Streaming contract

[`openapi.yaml`](openapi.yaml) is the complete public contract. Voice bodies are
raw PCM streams rather than JSON or WAV files.

| Direction | Format |
|---|---|
| Request | PCM16 little-endian, mono, 16 kHz, maximum 960,000 bytes |
| Response | PCM16 little-endian, mono, 24 kHz |

The Pi sends `X-Device-Guid` and the three required request audio headers. The
Function returns corresponding response audio headers before streaming bytes.
Errors that occur before response audio starts use the stable JSON error
envelope. An upstream error after streaming starts terminates the response
instead of pretending that truncated audio succeeded.
