# Home Assistant

The four lifecycle operations below are the supported deployment path. Each
install or uninstall command is idempotent: rerunning it with the same
arguments is safe. Application source is under `_src/`; run Azure commands
from that directory.

## 1. Install application on Pi

On Raspberry Pi OS, run the complete block below. It downloads the release
artifacts, verifies the checksum, and installs a production wake-word engine.
Rerunning the whole block upgrades or repairs the same installation without
replacing an existing configuration.

```bash
mkdir -p ~/home-assistant-install
cd ~/home-assistant-install
rm -f home-assistant-pi-bundle-1.0.1.tar.gz SHA256SUMS

curl -fL -o home-assistant-pi-bundle-1.0.1.tar.gz \
  https://github.com/khanasif1/jarvis-home-automation/releases/download/pi-v1.0.1/home-assistant-pi-bundle-1.0.1.tar.gz
curl -fL -o SHA256SUMS \
  https://github.com/khanasif1/jarvis-home-automation/releases/download/pi-v1.0.1/SHA256SUMS

sha256sum --check SHA256SUMS --ignore-missing
tar -xzf home-assistant-pi-bundle-1.0.1.tar.gz
sudo ./install.sh --version 1.0.1 --wakeword-extra openwakeword
```

The installer creates `/etc/home-assistant-pi/config.env` and waits to start
the service until the required values are present. After section 3 prints the
Azure API URL and device-provisioning command, set these values:

```bash
sudo nano /etc/home-assistant-pi/config.env
```

```dotenv
HAP_DEVICE_ID=<provisioned-device-id>
HAP_DEVICE_TOKEN=<provisioned-device-token>
HAP_API_BASE_URL=https://<function-app>.azurewebsites.net/api
HAP_WAKEWORD_ENGINE=openwakeword
```

Then start and verify the service:

```bash
sudo systemctl restart home-assistant-pi.service
sudo systemctl status home-assistant-pi.service --no-pager
```

## 2. Uninstall application on Pi

The retained release bundle provides a rerunnable uninstaller. `--purge`
removes the service, application, configuration, and dedicated system
user/group. Running it again when those resources are absent is a no-op.

```bash
cd ~/home-assistant-install
sudo ./uninstall.sh --purge
```

Without `--purge`, `/etc/home-assistant-pi/config.env` is preserved for a
future reinstall.

## 3. Install backend in Azure

Prerequisites:

- Python 3.11+
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
- An Azure subscription with permission to create resources and role assignments

Clone this repository, enter `_src/`, authenticate once, and run one lifecycle
command:

```bash
cd _src
az login
azd auth login
python3 infra/scripts/backend_lifecycle.py install \
  --environment-name home \
  --location australiaeast
```

On Windows PowerShell, use `python` instead of `python3`:

```powershell
Set-Location _src
az login
azd auth login
python infra\scripts\backend_lifecycle.py install `
  --environment-name home `
  --location australiaeast
```

This one command creates or updates the resource group, Function App, Storage,
Key Vault, Speech, Azure OpenAI, Application Insights, Log Analytics, and RBAC
assignments, then deploys the backend code and checks `/api/health`. It
generates an administrator key on first use and reuses it on later runs from
the ignored `_src/.azure/` environment. The final output includes the API URL,
storage account, and Bash/PowerShell device-provisioning commands. Run the one
for your shell; it prints the device ID and token once for the Pi configuration
in section 1.

## 4. Uninstall backend from Azure

This permanently deletes the environment resource group and every backend
service and data store inside it. The command uses Azure Developer CLI purge
mode for eligible soft-deleted dependencies, then verifies that the resource
group is gone. Repeating it is safe.

```bash
cd _src
az login
azd auth login
python3 infra/scripts/backend_lifecycle.py uninstall \
  --environment-name home \
  --yes
```

```powershell
Set-Location _src
az login
azd auth login
python infra\scripts\backend_lifecycle.py uninstall `
  --environment-name home `
  --yes
```

The local `_src/.azure/home/` settings are intentionally retained so a later
install can reuse the same environment settings. Key Vault purge protection
remains enabled; uninstall rotates the local resource-name seed so Azure's
retention of a protected deleted vault cannot block reinstalling the same
environment name.

Detailed design and troubleshooting remain in
[`_src/docs/architecture.md`](_src/docs/architecture.md),
[`_src/pi-client/README.md`](_src/pi-client/README.md), and
[`_src/infra/README.md`](_src/infra/README.md).
