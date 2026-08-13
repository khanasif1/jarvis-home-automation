# Jarvis Home Voice Assistant

Jarvis is a small half-duplex voice assistant for a 64-bit Raspberry Pi 3B. The
Pi performs only **“hey jarvis”** wake-word detection, WebRTC voice activity
detection, 16 kHz PCM capture, and 24 kHz PCM playback. An always-ready Azure
Function receives the live request stream and uses its managed identity to call
GPT Realtime in Microsoft Foundry. Storage and Foundry reject key
authentication.

The request audio starts uploading immediately; it is not recorded to a WAV
file. Azure Functions HTTP streaming is turn-based rather than full-duplex, so
the response begins after VAD closes the command, then plays on the Pi as each
response chunk arrives. A command ends after 1.2 seconds of silence or at the
30-second hard maximum.

## 1. Install application on Pi

**Prerequisites:** Raspberry Pi 3B, 64-bit Raspberry Pi OS Bookworm
(`dpkg --print-architecture` must return `arm64`), USB/I2S microphone, speaker,
internet access, and the **API base URL** plus **Device GUID** printed by
section 3.

```bash
mkdir -p ~/home-assistant-install
cd ~/home-assistant-install
rm -f home-assistant-pi-bundle-2.0.3.tar.gz SHA256SUMS

curl --fail --location \
  --output home-assistant-pi-bundle-2.0.3.tar.gz \
  https://github.com/khanasif1/jarvis-home-automation/releases/download/pi-v2.0.3/home-assistant-pi-bundle-2.0.3.tar.gz
curl --fail --location \
  --output SHA256SUMS \
  https://github.com/khanasif1/jarvis-home-automation/releases/download/pi-v2.0.3/SHA256SUMS

sha256sum --check SHA256SUMS --ignore-missing
tar -xzf home-assistant-pi-bundle-2.0.3.tar.gz

sudo ./install.sh \
  --version 2.0.3 \
  --api-url "https://YOUR-FUNCTION.azurewebsites.net/api" \
  --device-guid "YOUR-DEVICE-GUID"
```

The idempotent installer verifies the wheel, installs system/Python
dependencies, downloads only the “hey jarvis” TFLite model files, writes a
root-readable configuration, and starts `home-assistant-pi.service`. Rerun the
same command to repair or update the installation.

To upgrade an existing installation while explicitly selecting desktop user
`pi`, download/extract the current bundle as above, then run:

```bash
sudo ./update.sh --version 2.0.3 --runtime-user pi
```

This preserves the API URL and Device GUID. When migrating from release 2.0.1,
it clears the old account's numeric audio indexes and resolves devices again
inside `pi`'s PipeWire session.

```bash
sudo systemctl status home-assistant-pi.service --no-pager
sudo journalctl -u home-assistant-pi.service -n 100 --no-pager
sudo home-assistant-pi-service doctor
```

Version 2.0.3 runs in the invoking desktop user's PipeWire audio session and
automatically selects compatible defaults. Use `--runtime-user USER` when the
installer is invoked by a different administrator. If it selects the wrong
hardware, list devices in the service's exact environment and set
`HAP_INPUT_DEVICE` / `HAP_OUTPUT_DEVICE` in
`/etc/home-assistant-pi/config.env`:

```bash
sudo home-assistant-pi-service devices
sudo nano /etc/home-assistant-pi/config.env
sudo systemctl reset-failed home-assistant-pi.service
sudo systemctl restart home-assistant-pi.service
```

Use the numeric input/output indexes printed by `devices`, for example
`HAP_INPUT_DEVICE=1`. After restarting, confirm the service stays stable and
test one complete voice turn:

```bash
sleep 15
sudo systemctl show home-assistant-pi.service \
  --property=ActiveState,SubState,NRestarts
sudo home-assistant-pi-service doctor
sudo journalctl -u home-assistant-pi.service -n 50 --no-pager -l
```

Expected service values are `ActiveState=active`, `SubState=running`, and
`NRestarts=0`. Say **“hey jarvis”**, wait for the activation sound, ask a short
question, and confirm that spoken audio is returned. Follow live logs during
that test with:

Version 2.0.3 emits one correlated `activity` line at every live input/output
stage. To start with an empty view and monitor only new interaction activity:

```bash
sudo journalctl \
  --unit home-assistant-pi.service \
  --follow \
  --lines 0 \
  --output cat |
  grep --line-buffered 'activity'
```

The output updates immediately for wake detection, input speech start/end,
backend response, output playback start/end, completion, cancellation, and
failure. Each line includes the application timestamp and a `turn=` identifier.
Raw audio, spoken text, the Device GUID, and credentials are never logged.

To include startup, device, warning, and traceback messages too:

```bash
sudo journalctl \
  --unit home-assistant-pi.service \
  --follow \
  --lines 0 \
  --output short-iso-precise
```

## 2. Un install application on Pi

Run the retained release uninstaller. The first command preserves the API URL
and Device GUID for a later reinstall; the second removes them too. Both are
idempotent.

```bash
cd ~/home-assistant-install
sudo ./uninstall.sh
```

To remove all local application configuration:

```bash
cd ~/home-assistant-install
sudo ./uninstall.sh --purge-config
```

## 3. Install backend in Azure

**Prerequisites:** Python 3.11+, [Azure
CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), a subscription
with Foundry model quota, and permissions to create resources and role
assignments. No Azure Developer CLI or GitHub Actions are used.

From a clone of this repository:

```bash
git pull --ff-only origin main
cd _src
python3 infra/scripts/backend_lifecycle.py --version
az login
az account set --subscription "YOUR-SUBSCRIPTION-ID"

python3 infra/scripts/backend_lifecycle.py install \
  --environment-name home \
  --subscription-id "YOUR-SUBSCRIPTION-ID"
```

PowerShell:

```powershell
git pull --ff-only origin main
Set-Location _src
python infra\scripts\backend_lifecycle.py --version
az login
az account set --subscription "YOUR-SUBSCRIPTION-ID"

python infra\scripts\backend_lifecycle.py install `
  --environment-name home `
  --subscription-id "YOUR-SUBSCRIPTION-ID"
```

The version command must report `2.2.0 (private-storage-v1)` or newer. If it
does not recognize `--version`, the checkout predates private Storage support
and must not be deployed.

The command idempotently registers every required provider, including the
Application Insights smart-alert and private-network dependencies; validates
the selected Flex Consumption region and Foundry model; creates the resource
group, private identity-only Storage account, VNet and private endpoints,
Application Insights, Log Analytics, Microsoft Foundry resource/model,
always-ready Flex Consumption Function, and RBAC; waits for private storage
connectivity; deploys the backend; checks `/api/health`; and opens an
authenticated Foundry Realtime session to verify the async identity transport,
managed-identity RBAC, WebSocket handshake, and session configuration. Defaults are
`australiaeast` for the Function and `southindia` for the current
`gpt-realtime-2` availability. Override them with `--location` and
`--foundry-location` if your approved regions differ.

Copy the final **API base URL** and **Device GUID** into the Pi command in
section 1. They are preserved in
`~/.jarvis-home-automation/home.json`, so rerunning install keeps the same Pi
identity.

### If Azure reports `FlagMustBeSetForRestore`

Foundry account names remain reserved after deletion because Cognitive Services
uses soft delete. If an earlier or partially failed deployment deleted the
account but retained the same name, Azure reports
`FlagMustBeSetForRestore`.

If you do **not** need to restore that deleted account, permanently purge it
using the account name, resource group, and location shown in the error.
**Purge cannot be undone.**

```bash
az cognitiveservices account purge \
  --name "YOUR-SOFT-DELETED-FOUNDRY-NAME" \
  --resource-group "rg-home-jarvis" \
  --location "southindia"
```

PowerShell:

```powershell
az cognitiveservices account purge `
  --name "YOUR-SOFT-DELETED-FOUNDRY-NAME" `
  --resource-group "rg-home-jarvis" `
  --location "southindia"
```

After the purge completes, rerun the same install command. The installer is
idempotent and reuses the environment's Device GUID and resource-name seed.

### If zip deployment reports Storage HTTP 403

The current installer deploys VNet integration, private endpoints, and private
DNS, then verifies the integration, endpoints, and Storage RBAC before
uploading code. A direct zip-deployment 403 with no private endpoints indicates
that an older checkout was run. Return to the repository root, run
`git pull --ff-only origin main`, confirm the installer version above, and rerun
the same install command. Do not add Storage keys, enable shared-key
authentication, or enable public Storage access.

## 4. UnInstall backend from azure

This deletes the resource group and therefore the Function, Foundry deployment,
Storage account, monitoring, and role assignments, then purges the Foundry
account from soft delete. It is idempotent and requires `--yes` to prevent
accidental deletion.

```bash
cd _src
python3 infra/scripts/backend_lifecycle.py uninstall \
  --environment-name home \
  --subscription-id "YOUR-SUBSCRIPTION-ID" \
  --yes
```

PowerShell:

```powershell
Set-Location _src
python infra\scripts\backend_lifecycle.py uninstall `
  --environment-name home `
  --subscription-id "YOUR-SUBSCRIPTION-ID" `
  --yes
```

The local Device GUID is retained for the Pi. The resource-name seed is rotated
after deletion as an additional safeguard against stale global resource names.

See [`_src/docs/architecture.md`](_src/docs/architecture.md) for the design and
[`_src/contracts/openapi.yaml`](_src/contracts/openapi.yaml) for the wire
contract.
