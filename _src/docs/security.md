# Security

## Azure authentication

- Microsoft Foundry has `disableLocalAuth: true`.
- Storage has `allowSharedKeyAccess: false` and OAuth as its default.
- The Function obtains a token for `https://ai.azure.com/.default` through its
  system-assigned managed identity.
- RBAC grants only `Cognitive Services OpenAI User` on Foundry and the
  Functions host data roles on its one Storage account. Application Insights
  ingestion uses `Authorization=AAD` and `Monitoring Metrics Publisher`.
- No AI key, Storage key, Speech key, SAS token, client secret, or Key Vault
  reference is present in source or app settings. The Application Insights
  connection string identifies the telemetry endpoint; local ingestion
  authentication is disabled and Microsoft Entra authorizes writes.
- FTP and SCM basic publishing credentials are disabled.

The Storage account is still required by the Azure Functions runtime and
deployment system. It contains no Jarvis conversation, reminder, user, or
device tables.

## Pi authentication

The approved device mechanism is one fixed random UUID sent as
`X-Device-Guid`. The Function requires canonical lowercase form and compares it
in constant time. The lifecycle command creates it once and the Pi installer
stores it in a root-owned `0640` file. HTTPS is mandatory outside local
development.

A UUID is a simple shared credential, not hardware attestation. Anyone who
obtains it can call the voice endpoint until it is changed in Azure and on the
Pi. This is an explicit simplicity tradeoff for one home device. Do not put the
GUID in logs, screenshots, issue reports, or source control.

## Data handling

- Microphone audio exists in bounded memory while it streams.
- The application never writes command or response audio to disk.
- The backend creates one Foundry session per request and closes it on success,
  failure, or client cancellation.
- The backend logs failure categories but not audio, the device GUID, model
  transcripts, or response content.
- No conversation history persists between turns.

## Public surface

`GET /api/health` is intentionally public and returns only `{"status":"ok"}`.
`POST /api/voice/stream` is public at the network layer but requires the device
GUID. Audio type, rate, channel count, width, sample alignment, and the
30-second/960,000-byte limit are validated before a model response is returned.

For an internet-facing multi-device or commercial deployment, replace the UUID
with per-device certificates or Microsoft Entra workload identities and add
edge rate limiting. Those controls are intentionally outside this single-Pi
solution.
