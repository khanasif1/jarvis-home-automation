# Security

## Trust boundaries

- Every device has a unique identifier and high-entropy bearer token.
- Device tokens are provisioned out of band, stored in a root-readable file,
  and never written to logs, diagnostics output, images, or service units.
- The public health endpoint contains no configuration data. Voice requests
  require authentication and are size-limited before processing.
- Azure resources use managed identity and role assignments. Secrets belong in
  Key Vault or platform application settings backed by Key Vault references.
- The production Function host and Table repositories use identity-based
  Storage connections; shared-key access on the Storage account is disabled.
- FTP/SCM basic publishing credentials are disabled. CI deploys with GitHub
  OIDC and Azure RBAC.
- Google refresh tokens are encrypted at rest and never returned to the Pi.

## Least privilege

The Pi service uses a dedicated non-login account in the `audio` group. Its
configuration is owned by root and readable by the service group only. It does
not run as root. The Function App receives only the data-plane roles required
for Storage, Key Vault, Speech, and Azure OpenAI.

## Data handling

Audio is held in memory or a restrictive temporary file and deleted after each
turn. Conversation audio persistence is disabled. Logs use correlation IDs and
redact authorization headers, tokens, credentials, message bodies, and audio.
Retention for Application Insights and Storage is configured in infrastructure.

## Operational requirements

- Rotate a device token immediately when a Pi is lost or rebuilt.
- Restrict the Function App with network controls where the deployment allows.
- Use workload identity federation for CI; do not store long-lived Azure
  credentials in repository secrets.
- Enable GitHub environment protection for production infrastructure and
  backend deployment.
- Review dependency alerts and rebuild Pi bundles rather than modifying an
  installed environment manually.
