# Home Assistant

Voice-first home automation with a lightweight Raspberry Pi client, an
independently deployed Azure Functions backend, and independently provisioned
Azure infrastructure.

## Component boundaries

| Component | Purpose | Deploys to |
| --- | --- | --- |
| [`pi-client/`](pi-client/) | Wake word, audio capture/playback, API client, reminders, and device diagnostics | Raspberry Pi |
| [`azure-backend/`](azure-backend/) | Authenticated voice-turn API, speech/AI orchestration, tools, and integrations | Azure Functions |
| [`infra/`](infra/) | Bicep modules, role assignments, provisioning, and device bootstrap | Azure |
| [`contracts/`](contracts/) | Versioned OpenAPI and JSON Schema definitions used by both runtime components | Build-time input |

The Pi package never imports backend source. The backend can be built and
deployed without downloading or building the Pi client, and infrastructure
validation does not build either runtime.

## Raspberry Pi installation (no Git required)

These commands install release `1.0.0` directly from GitHub Releases:
For a fork or a different release, replace `khanasif1`,
`jarvis-home-automation`, and `1.0.0` with the matching owner, repository, and
version.

```bash
mkdir -p ~/home-assistant-install
cd ~/home-assistant-install

curl --fail --location \
  --output home-assistant-pi-bundle-1.0.0.tar.gz \
  https://github.com/khanasif1/jarvis-home-automation/releases/download/pi-v1.0.0/home-assistant-pi-bundle-1.0.0.tar.gz
curl --fail --location \
  --output SHA256SUMS \
  https://github.com/khanasif1/jarvis-home-automation/releases/download/pi-v1.0.0/SHA256SUMS

sha256sum --check SHA256SUMS --ignore-missing
tar -xzf home-assistant-pi-bundle-1.0.0.tar.gz
sudo ./install.sh --version 1.0.0
```

For a private repository, authenticate the GitHub CLI on the Pi and download
the release without putting credentials in a command, file, image, installer,
or service:

```bash
gh auth login
gh release download pi-v1.0.0 \
  --repo khanasif1/jarvis-home-automation \
  --pattern 'home-assistant-pi-bundle-1.0.0.tar.gz' \
  --pattern 'SHA256SUMS'
```

Inspect the downloaded installer before running it. Never use
`curl URL | sudo bash`.

## Independent developer operations

Python 3.11 or newer is recommended for development.

```bash
# Keep imported bytecode with the other disposable validation output.
export PYTHONPYCACHEPREFIX="$PWD/.test-artifacts/pycache"

# Build only the Pi client
mkdir -p .test-artifacts/pi-build/source
cp pi-client/pyproject.toml pi-client/README.md .test-artifacts/pi-build/source/
cp -a pi-client/src .test-artifacts/pi-build/source/
python -m build .test-artifacts/pi-build/source \
  --outdir .test-artifacts/pi-dist

# Test only the Pi client
pytest pi-client/tests --basetemp=.test-artifacts/pytest/pi-client

# Run the backend locally
(cd azure-backend && func start)

# Test only the backend (from the repository root)
pytest azure-backend/tests --basetemp=.test-artifacts/pytest/backend

# Validate only infrastructure
mkdir -p .test-artifacts/bicep
az bicep build --file infra/main.bicep \
  --outfile .test-artifacts/bicep/main.json

# Provision only infrastructure
export ADMIN_API_KEY="$(openssl rand -base64 48)"
azd provision

# Deploy only the backend
azd deploy azure-backend

# Create a Pi release
git tag pi-v1.0.0
git push origin pi-v1.0.0
```

Local test output, coverage data, temporary recordings, build products, and
validation output must be written under `.test-artifacts/`. It is ignored by
Git and can be deleted as one folder before production rollout. Test source
remains next to each independently buildable component.

The production backend workflow uses GitHub OIDC rather than a client secret.
Configure the protected `production` environment with the `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` secrets plus the
`AZURE_ENV_NAME`, `AZURE_LOCATION`, `AZURE_BACKEND_NAME`, and
`AZURE_RESOURCE_GROUP` variables. The last two values are emitted by the
infrastructure deployment.

```bash
rm -rf .test-artifacts
```

```powershell
Remove-Item -Recurse -Force .test-artifacts
```

See [`docs/architecture.md`](docs/architecture.md) for the full design and the
component READMEs for setup and operational commands.
