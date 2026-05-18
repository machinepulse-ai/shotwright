ARG BASE_IMAGE=mcr.microsoft.com/windows/server:ltsc2025
ARG http_proxy
ARG https_proxy
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG PIP_DEFAULT_TIMEOUT=120
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG CHOCO_SOURCE
ARG AE_SETUP_IMAGE=ghcr.io/machinepulse-ai/shotwright/after-effects-setup:26.2

# =============================================================================
# Stage: base — shared toolchain for all targets
# =============================================================================
FROM ${BASE_IMAGE} AS base

ARG http_proxy
ARG https_proxy
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG PIP_DEFAULT_TIMEOUT
ARG NPM_REGISTRY
ARG CHOCO_SOURCE

ENV http_proxy=${http_proxy} \
      https_proxy=${https_proxy} \
      HTTP_PROXY=${HTTP_PROXY} \
      HTTPS_PROXY=${HTTPS_PROXY} \
      PIP_INDEX_URL=${PIP_INDEX_URL} \
      PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
      PIP_DEFAULT_TIMEOUT=${PIP_DEFAULT_TIMEOUT} \
      NPM_CONFIG_REGISTRY=${NPM_REGISTRY} \
      CHOCO_SOURCE=${CHOCO_SOURCE}

SHELL ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]

# Install Chocolatey
RUN Set-ExecutionPolicy Bypass -Scope Process -Force; \
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; \
    $ProgressPreference = 'SilentlyContinue'; \
    $proxy = if ($env:https_proxy) { $env:https_proxy } elseif ($env:http_proxy) { $env:http_proxy } elseif ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { $env:HTTP_PROXY }; \
    $webClient = New-Object System.Net.WebClient; \
    if ($proxy) { $webClient.Proxy = New-Object System.Net.WebProxy($proxy, $true) }; \
    iex ($webClient.DownloadString('https://community.chocolatey.org/install.ps1'))

COPY scripts/install/ensure_pwsh_compat.ps1 C:/bootstrap/ensure_pwsh_compat.ps1

# Common tools (--no-progress suppresses the progress-bar spam)
RUN $chocoArgs = @('install', '-y', '--no-progress'); \
      if (-not [string]::IsNullOrWhiteSpace($env:CHOCO_SOURCE)) { $chocoArgs += @('--source', $env:CHOCO_SOURCE) }; \
      $chocoArgs += @('ffmpeg', 'git', 'nodejs', 'python313', 'vcredist-all', 'vim'); \
      & choco @chocoArgs

RUN & 'C:/bootstrap/ensure_pwsh_compat.ps1'

RUN if (-not [string]::IsNullOrWhiteSpace($env:PIP_INDEX_URL)) { \
            & python -m pip config --global set global.index-url $env:PIP_INDEX_URL; \
      }; \
      if (-not [string]::IsNullOrWhiteSpace($env:PIP_TRUSTED_HOST)) { \
            & python -m pip config --global set global.trusted-host $env:PIP_TRUSTED_HOST; \
      }; \
      if (-not [string]::IsNullOrWhiteSpace($env:NPM_CONFIG_REGISTRY)) { \
            & 'C:/Program Files/nodejs/npm.cmd' config set registry $env:NPM_CONFIG_REGISTRY; \
      }

RUN & python -m pip install --no-cache-dir --quiet --retries 10 --timeout $env:PIP_DEFAULT_TIMEOUT psutil

# =============================================================================
# Stage: after-effects-setup — prebuilt AE installer payload from GHCR
# =============================================================================
FROM ${AE_SETUP_IMAGE} AS after-effects-setup

# =============================================================================
# Stage: shotwright — all-in-one app + AE runtime container (default target)
# =============================================================================
FROM base AS shotwright

ARG INSTALL_NVM=0
ARG AUTO_INSTALL_AFTER_EFFECTS=1

ENV INSTALL_NVM=${INSTALL_NVM} \
      AUTO_INSTALL_AFTER_EFFECTS=${AUTO_INSTALL_AFTER_EFFECTS} \
      SHOTWRIGHT_AUTO_INSTALL_AFTER_EFFECTS=${AUTO_INSTALL_AFTER_EFFECTS}

RUN if ($env:INSTALL_NVM -eq '1') { choco install -y --no-progress nvm.install } else { Write-Host 'Skipping optional nvm.install'; }

RUN & 'C:/Program Files/nodejs/npm.cmd' install -g \
      @nexrender/action-copy@1.49.4 \
      @nexrender/action-encode@1.46.8 \
      @nexrender/cli@1.63.3

WORKDIR C:/workspace
COPY AGENTS.md C:/workspace/AGENTS.md
COPY keepalive.ps1 C:/workspace/keepalive.ps1
COPY shotwright-config.json C:/workspace/shotwright-config.json
COPY setup-versions.yml C:/workspace/setup-versions.yml
COPY scripts/install/install_after_effects_in_container.ps1 C:/workspace/scripts/install/install_after_effects_in_container.ps1
COPY scripts/install/modify_setup_win.py C:/workspace/scripts/install/modify_setup_win.py
COPY scripts/install/setup_versions.py C:/workspace/scripts/install/setup_versions.py
COPY validation-data/templates/validation_motion.aep C:/workspace/validation-data/templates/validation_motion.aep
COPY --from=after-effects-setup C:/payload C:/data/payload

RUN & 'C:/workspace/scripts/install/install_after_effects_in_container.ps1' -RequirePayload

COPY scripts/ C:/workspace/scripts/
RUN & 'C:/workspace/scripts/install/install_open_fonts.ps1'

COPY src/backend/pyproject.toml src/backend/.python-version C:/workspace/src/backend/
RUN & python -m pip install --no-cache-dir --quiet --retries 10 --timeout $env:PIP_DEFAULT_TIMEOUT uv; \
      & python C:/workspace/scripts/install/install_pyproject_dependencies.py C:/workspace/src/backend/pyproject.toml --index-url $env:PIP_INDEX_URL

COPY src/backend/requirements-aigc.txt C:/workspace/src/backend/requirements-aigc.txt
COPY src/backend/codex-bridge/package.json src/backend/codex-bridge/package-lock.json C:/workspace/src/backend/codex-bridge/
RUN Set-Location C:/workspace/src/backend/codex-bridge; \
      & 'C:/Program Files/nodejs/npm.cmd' ci --omit=dev --no-progress --fetch-retries 5 --fetch-timeout 120000

COPY src/frontend/package.json src/frontend/package-lock.json C:/workspace/src/frontend/
RUN Set-Location C:/workspace/src/frontend; \
      & 'C:/Program Files/nodejs/npm.cmd' ci --no-progress --fetch-retries 5 --fetch-timeout 120000

COPY src/backend/app/ C:/workspace/src/backend/app/
COPY src/backend/codex-bridge/ C:/workspace/src/backend/codex-bridge/
COPY src/frontend/ C:/workspace/src/frontend/
COPY src/scripts/ C:/workspace/src/scripts/

ENV SHOTWRIGHT_PYTHON_TOOL_AUTO_SYNC_DEPENDENCIES=true \
      SHOTWRIGHT_PYTHON_TOOL_RUNTIME_DIR=C:/data/python \
      SHOTWRIGHT_PYTHON_TOOL_REQUIREMENTS=C:/workspace/src/backend/requirements-aigc.txt

EXPOSE 3000 8000

CMD ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "C:/workspace/scripts/runtime_entrypoint.ps1"]

# =============================================================================
# Stage: backend — FastAPI API server
# =============================================================================
FROM base AS backend

RUN $ProgressPreference = 'SilentlyContinue'; \
      & python -m pip install --no-cache-dir --quiet --retries 10 --timeout $env:PIP_DEFAULT_TIMEOUT uv

WORKDIR C:/workspace

COPY AGENTS.md C:/workspace/AGENTS.md
COPY shotwright-config.json C:/workspace/shotwright-config.json
COPY setup-versions.yml C:/workspace/setup-versions.yml
COPY scripts/install/ensure_pwsh_compat.ps1 C:/workspace/scripts/install/ensure_pwsh_compat.ps1
COPY scripts/install/install_pyproject_dependencies.py C:/workspace/scripts/install/install_pyproject_dependencies.py
COPY validation-data/templates/validation_motion.aep C:/workspace/validation-data/templates/validation_motion.aep
COPY src/backend/pyproject.toml src/backend/.python-version C:/workspace/src/backend/
COPY src/backend/requirements-aigc.txt C:/workspace/src/backend/requirements-aigc.txt
RUN & python C:/workspace/scripts/install/install_pyproject_dependencies.py C:/workspace/src/backend/pyproject.toml --index-url $env:PIP_INDEX_URL
COPY src/backend/codex-bridge/package.json src/backend/codex-bridge/package-lock.json C:/workspace/src/backend/codex-bridge/
RUN Set-Location C:/workspace/src/backend/codex-bridge; \
      & 'C:/Program Files/nodejs/npm.cmd' ci --omit=dev --no-progress --fetch-retries 5 --fetch-timeout 120000
COPY scripts/ C:/workspace/scripts/
COPY src/backend/app/ C:/workspace/src/backend/app/
COPY src/backend/codex-bridge/ C:/workspace/src/backend/codex-bridge/
WORKDIR C:/workspace/src/backend

ENV SHOTWRIGHT_PYTHON_TOOL_AUTO_SYNC_DEPENDENCIES=true \
      SHOTWRIGHT_PYTHON_TOOL_RUNTIME_DIR=C:/data/python \
      SHOTWRIGHT_PYTHON_TOOL_REQUIREMENTS=C:/workspace/src/backend/requirements-aigc.txt

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# =============================================================================
# Stage: frontend-build — webpack production build
# =============================================================================
FROM base AS frontend-build

WORKDIR C:/frontend

COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN & 'C:/Program Files/nodejs/npm.cmd' ci --no-progress

COPY src/frontend/ ./
RUN & 'C:/Program Files/nodejs/npm.cmd' run build

# =============================================================================
# Stage: frontend — static file server
# =============================================================================
FROM base AS frontend

RUN & 'C:/Program Files/nodejs/npm.cmd' install -g serve@14

WORKDIR C:/frontend
COPY --from=frontend-build C:/frontend/dist ./dist/

EXPOSE 3000
CMD ["cmd", "/c", "serve", "-s", "C:\\frontend\\dist", "-l", "3000"]

# =============================================================================
# Stage: final — keep plain docker build aligned with the AE runtime image
# =============================================================================
FROM shotwright AS final
