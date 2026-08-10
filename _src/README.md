# Source

The deployable solution is intentionally small:

```text
azure-backend/  Python Azure Function that bridges PCM to Foundry Realtime
contracts/      HTTP streaming contract
docs/           Architecture and security decisions
infra/          Bicep and the manual Azure lifecycle command
pi-client/      Raspberry Pi wake-word, VAD, streaming, and release tooling
```

There are no committed tests, recordings, CI workflows, Google integrations,
device registration services, or application databases. Disposable validation
and build output belongs only in `_src/.test-artifacts/`, which is ignored.

Deployment commands are in the [root README](../README.md).
