# Azure infrastructure

`main.bicep` creates exactly these services:

- Linux Flex Consumption Function App, Python 3.11
- one always-ready HTTP instance and maximum scale-out of 40
- system-assigned managed identity
- Functions host/deployment Storage account
- Microsoft Foundry `AIServices` resource with one `gpt-realtime-2` deployment
- Application Insights and Log Analytics
- data-plane RBAC for host Storage and Foundry

Security controls are declarative:

- Foundry: `disableLocalAuth: true`
- Storage: `allowSharedKeyAccess: false` and
  `defaultToOAuthAuthentication: true`
- Function: FTP/SCM basic publishing credentials disabled
- Function-to-Foundry and Function-to-Storage access uses managed identity
- Application Insights local authentication is disabled; telemetry uses Entra
- no Key Vault, Speech service, account key, application table, or
  device-registration store

## Lifecycle

From `_src/`, install or update everything, including backend code:

```bash
python3 infra/scripts/backend_lifecycle.py install \
  --environment-name home \
  --subscription-id YOUR-SUBSCRIPTION-ID
```

Default regions are `australiaeast` for the Function and `southindia` for
Foundry. Pass `--location` and `--foundry-location` to override them. Install
registers the monitoring alert dependency and validates both regions plus the
configured model before creating or updating resources.

If Azure reports `FlagMustBeSetForRestore`, a prior Foundry account with the
same name is soft-deleted. If that account is no longer needed, permanently
purge it, then rerun install:

```bash
az cognitiveservices account purge \
  --name YOUR-SOFT-DELETED-FOUNDRY-NAME \
  --resource-group rg-home-jarvis \
  --location southindia
```

Purge cannot be undone. Use the account name, resource group, and location from
the Azure error; see the root README for PowerShell syntax.

Delete every Azure service in the environment:

```bash
python3 infra/scripts/backend_lifecycle.py uninstall \
  --environment-name home \
  --subscription-id YOUR-SUBSCRIPTION-ID \
  --yes
```

The lifecycle state is stored with restricted permissions under
`~/.jarvis-home-automation/`. Install recovers the UUID and name seed from an
existing resource group when possible, so running from a second checkout does
not silently replace the Pi identity. Uninstall purges the Foundry account from
soft delete and rotates the name seed.
