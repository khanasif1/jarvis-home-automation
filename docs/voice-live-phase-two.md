# Voice Live phase two

The initial release uses bounded request/response turns because they are easier
to secure, retry, meter, and run on constrained Pi hardware.

Phase two may add a bidirectional streaming transport after the following gates:

- Per-device connection authentication and revocation.
- Backpressure, reconnect, and sequence semantics documented in `contracts/`.
- Server-side turn cancellation and interruption handling.
- Audio buffers bounded in memory with persistence disabled.
- Regional availability, quota, and cost confirmed for the selected service.
- End-to-end latency, packet-loss, and long-session soak tests.
- A fallback to the existing `/voice-turn` endpoint.

The streaming client must remain an optional Pi extra so the default wheel does
not gain heavyweight dependencies.
