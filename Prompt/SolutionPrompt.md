# Mandatory component separation and lightweight Raspberry Pi distribution

The Raspberry Pi must not need to clone or download the complete repository.

Although development may use one monorepo, the solution must consist of three independently buildable and deployable components:

1. pi-client
   - Contains only code needed on the Raspberry Pi.
   - Builds into a small Python wheel and a versioned Raspberry Pi release bundle.
   - Can be downloaded and installed independently.
   - Must not contain Azure backend source, Bicep files, backend tests, or documentation unrelated to the Pi.

2. azure-backend
   - Contains the Azure Functions application and backend tests.
   - Is deployed independently.
   - Must not be copied to or installed on the Raspberry Pi.

3. infra
   - Contains all Azure Infrastructure as Code.
   - Includes Bicep modules, Azure Developer CLI configuration, deployment scripts, and role assignments.
   - Must remain separate from both runtime applications.

The Pi installation process must download only the versioned pi-client release artifact. It must not use git clone, download a repository ZIP, or require Azure deployment source.

# Revised repository structure

Create this structure:

home-assistant/
  README.md
  .gitignore
  azure.yaml

  contracts/
    openapi.yaml
    schemas/
      voice-turn-request.json
      voice-turn-response.json
      error-response.json

  pi-client/
    README.md
    pyproject.toml
    requirements-runtime.txt
    requirements-dev.txt
    .env.example
    CHANGELOG.md
    src/
      home_assistant_pi/
        __init__.py
        __main__.py
        main.py
        cli.py
        config.py
        version.py
        state_machine.py
        logging_config.py
        audio/
          __init__.py
          capture.py
          playback.py
          vad.py
          wav.py
        wakeword/
          __init__.py
          base.py
          porcupine.py
          keyboard.py
          openwakeword.py
        api/
          __init__.py
          client.py
          models.py
        reminders/
          __init__.py
          poller.py
        assets/
          activation.wav
          cancellation.wav
          offline.wav
    tests/
    scripts/
      install.sh
      uninstall.sh
      update.sh
      test_microphone.py
      test_speaker.py
      list_audio_devices.py
      run_push_to_talk.py
    packaging/
      build-release.ps1
      build-release.sh
      release-manifest.json
    systemd/
      home-assistant.service

  azure-backend/
    README.md
    function_app.py
    host.json
    local.settings.example.json
    requirements.txt
    requirements-dev.txt
    src/
      home_assistant_api/
        __init__.py
        config.py
        auth.py
        errors.py
        models.py
        speech/
          stt.py
          tts.py
        ai/
          orchestrator.py
          prompt.py
          tool_definitions.py
          tool_executor.py
        tools/
          todos.py
          reminders.py
          google_calendar.py
          google_tasks.py
          gmail.py
        repositories/
          todos.py
          reminders.py
          sessions.py
          devices.py
          idempotency.py
        google/
          oauth.py
          credentials.py
          calendar_client.py
          tasks_client.py
          gmail_client.py
        telemetry.py
        time_utils.py
    prompts/
      assistant_system.txt
    tests/
      unit/
      integration/

  infra/
    README.md
    main.bicep
    main.parameters.json
    modules/
      function-app.bicep
      storage.bicep
      key-vault.bicep
      monitoring.bicep
      speech.bicep
      openai.bicep
      role-assignments.bicep
    scripts/
      deploy.ps1
      deploy.sh
      provision-device.ps1
      provision-device.sh

  docs/
    architecture.md
    security.md
    google-oauth-setup.md
    voice-live-phase-two.md

  .github/
    workflows/
      pi-client-ci.yml
      pi-client-release.yml
      azure-backend-ci.yml
      azure-backend-deploy.yml
      infrastructure-validate.yml

Each component must have its own README, dependency files, tests, build commands, and CI workflow.

The pi-client package must not import code from azure-backend. Shared API definitions must come from contracts/openapi.yaml or be included as generated models inside the Pi wheel.

# Raspberry Pi release packaging

Create a GitHub Actions workflow named pi-client-release.yml.

When a tag matching pi-v* is pushed, the workflow must:

1. Run Pi-client unit tests.
2. Build the Python wheel.
3. Build a source distribution for development use.
4. Build a lightweight Pi release archive.
5. Generate SHA-256 checksums.
6. Publish the files to a GitHub Release.

Release files should resemble:

home-assistant-pi-1.0.0-py3-none-any.whl
home-assistant-pi-1.0.0.tar.gz
home-assistant-pi-bundle-1.0.0.tar.gz
SHA256SUMS

The Pi bundle must contain only:

- The Pi-client wheel.
- install.sh.
- update.sh.
- uninstall.sh.
- The systemd unit template.
- A Pi-specific environment-file example.
- Runtime dependency metadata.
- Release version and checksum metadata.
- Small bundled notification sounds.

It must not contain:

- Azure backend source.
- Bicep templates.
- Azure deployment scripts.
- Backend dependencies.
- Backend tests.
- Git history.
- Development dependencies.
- Test recordings.
- Temporary audio.
- Build caches.

Keep the release bundle as small as practical.

Do not package a Python virtual environment because virtual environments are not reliably portable across Raspberry Pi OS versions and CPU architectures.

# Raspberry Pi installation

The normal installation must not require git.

Document an installation flow similar to:

mkdir -p ~/home-assistant-install
cd ~/home-assistant-install

curl -L \
  -o home-assistant-pi-bundle.tar.gz \
  https://github.com/OWNER/REPOSITORY/releases/download/pi-v1.0.0/home-assistant-pi-bundle-1.0.0.tar.gz

curl -L \
  -o SHA256SUMS \
  https://github.com/OWNER/REPOSITORY/releases/download/pi-v1.0.0/SHA256SUMS

sha256sum --check SHA256SUMS --ignore-missing

tar -xzf home-assistant-pi-bundle.tar.gz
sudo ./install.sh --version 1.0.0

The documentation must tell the user to replace OWNER, REPOSITORY, and version values.

For private repositories, document a secure authenticated download method. Never embed a GitHub token in the installer, source code, image, or systemd configuration.

The install script must:

1. Detect Raspberry Pi architecture:
   - armv7l
   - aarch64
2. Check the supported Raspberry Pi OS and Python version.
3. Install only required operating-system packages.
4. Create a dedicated system user, such as homeassistant.
5. Give that user only the audio-group permissions it requires.
6. Create the application directory:
   /opt/home-assistant-pi
7. Create a virtual environment:
   /opt/home-assistant-pi/venv
8. Install the wheel using:
   pip install --no-cache-dir
9. Avoid installing development and test dependencies.
10. Copy the systemd service file.
11. Create:
    /etc/home-assistant-pi/config.env
12. Set restrictive ownership and mode on configuration files.
13. Preserve an existing configuration file during upgrades.
14. Enable the systemd service.
15. Start the application only after configuration has been completed.
16. Delete downloaded build files and temporary package caches after successful installation.
17. Print the exact commands needed to configure and start the service.
18. Be safe to run more than once.
19. Exit with a clear error if installation cannot be completed.
20. Never silently continue after a failed dependency installation.

Do not use:

curl URL | sudo bash

Download the installer first so the user can verify its checksum and inspect it before execution.

# Raspberry Pi update process

Create update.sh with this behavior:

sudo ./update.sh --version 1.1.0

It must:

1. Download only the requested Pi release bundle.
2. Verify its SHA-256 checksum.
3. Stop the systemd service.
4. Preserve:
   - Device ID.
   - Device token file.
   - Timezone.
   - Audio-device settings.
   - Wake-word configuration.
5. Create a rollback copy of the currently installed wheel or environment metadata.
6. Install the new wheel with --no-cache-dir.
7. Restart the service.
8. Check that the process remains active.
9. Roll back to the previous release if startup fails.
10. Delete temporary files after success or rollback.
11. Never download the Azure backend or infrastructure folders.

Also provide a CLI command such as:

home-assistant-pi --version
home-assistant-pi doctor
home-assistant-pi test-microphone
home-assistant-pi test-speaker

The doctor command must report configuration and hardware status without displaying secrets.

# Disk-space requirements

Optimize the Pi deployment for limited storage:

1. Do not install backend dependencies on the Pi.
2. Do not install test or lint dependencies on the Pi.
3. Use pip --no-cache-dir.
4. Remove temporary WAV files immediately.
5. Keep raw-audio persistence disabled.
6. Do not persist conversation audio.
7. Use journald instead of writing unlimited application log files.
8. Document how to configure journald size limits.
9. Keep only the current and one previous application version for rollback.
10. Remove stale release downloads after successful updates.
11. Avoid Docker on the Raspberry Pi.
12. Avoid cloning the repository.
13. Avoid local Azure SDK packages unless the Pi directly requires them.
14. Include a command that reports installed application disk usage.

# Independent Azure backend deployment

The Azure backend must be deployable without building or downloading the Pi client.

The azure-backend CI workflow must trigger only when relevant files change:

- azure-backend/**
- contracts/**
- workflow configuration related to the backend

The deployment package must contain only:

- Azure Functions runtime code.
- Backend production dependencies.
- Assistant system prompt.
- Required generated API models.

It must not contain:

- Pi-client source or dependencies.
- Pi hardware libraries.
- Pi audio assets.
- Infrastructure source unless required by the deployment workflow.
- Backend test dependencies.

# Independent Infrastructure as Code deployment

All Azure Infrastructure as Code must be under infra/.

The infra folder must support:

azd provision

or:

az deployment sub create

Infrastructure deployment must not require building the Pi client.

The infra folder must include:

- Bicep templates.
- Bicep modules.
- Environment parameter documentation.
- Role assignments.
- Outputs needed by the backend.
- Outputs needed to configure the Pi, such as the backend API URL.
- Device-provisioning scripts.
- Validation commands.

Do not place Azure Function implementation code inside infra/.

Do not place Bicep templates inside azure-backend/.

Deployment scripts may reference azure-backend as the application source, but infrastructure provisioning and backend code deployment must remain logically separate operations.

# Root-level developer commands

Provide root-level documentation for independent operations:

Build only the Pi client:

python -m build pi-client

Test only the Pi client:

pytest pi-client/tests

Build only the backend:

cd azure-backend
func start

Test only the backend:

pytest azure-backend/tests

Validate only the infrastructure:

az bicep build --file infra/main.bicep

Deploy only the Azure infrastructure:

azd provision

Deploy only the Azure backend:

azd deploy azure-backend

Create a Pi release:

git tag pi-v1.0.0
git push origin pi-v1.0.0

The exact commands may be adjusted to match the completed implementation, but component independence must be preserved.

# Additional acceptance criteria

The solution is not complete until all these conditions are satisfied:

1. The Pi can be installed without git.
2. The Pi can be installed without downloading the complete repository.
3. The Pi release contains no backend or Bicep source.
4. The Pi installs only runtime dependencies.
5. The Pi installer uses --no-cache-dir.
6. The Pi configuration survives an upgrade.
7. A failed update can roll back to the previous version.
8. Release files have SHA-256 checksums.
9. The Pi service runs from its own virtual environment.
10. The Azure backend can be built and deployed without the Pi source.
11. Azure infrastructure can be validated and provisioned independently.
12. CI workflows use path filters to avoid unnecessary builds.
13. The pi-client has no runtime dependency on azure-backend source.
14. The full repository is required only for developers, not for the Raspberry Pi.
15. Documentation clearly distinguishes:
    - Pi installation.
    - Azure backend deployment.
    - Azure infrastructure provisioning.
