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

# Common tools (--no-progress suppresses the progress-bar spam)
RUN $chocoArgs = @('install', '-y', '--no-progress'); \
      if (-not [string]::IsNullOrWhiteSpace($env:CHOCO_SOURCE)) { $chocoArgs += @('--source', $env:CHOCO_SOURCE) }; \
      $chocoArgs += @('ffmpeg', 'git', 'nodejs', 'python313', 'vcredist-all', 'vim'); \
      & choco @chocoArgs

RUN if (-not [string]::IsNullOrWhiteSpace($env:PIP_INDEX_URL)) { \
            & python -m pip config --global set global.index-url $env:PIP_INDEX_URL; \
      }; \
      if (-not [string]::IsNullOrWhiteSpace($env:PIP_TRUSTED_HOST)) { \
            & python -m pip config --global set global.trusted-host $env:PIP_TRUSTED_HOST; \
      }; \
      if (-not [string]::IsNullOrWhiteSpace($env:NPM_CONFIG_REGISTRY)) { \
            & 'C:/Program Files/nodejs/npm.cmd' config set registry $env:NPM_CONFIG_REGISTRY; \
      }

# =============================================================================
# Stage: shotwright — AE runtime container (default target)
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
COPY keepalive.ps1 C:/workspace/keepalive.ps1
COPY shotwright-config.json C:/workspace/shotwright-config.json
COPY setup-versions.yml C:/workspace/setup-versions.yml
COPY scripts/ C:/workspace/scripts/

CMD ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "C:/workspace/scripts/runtime_entrypoint.ps1"]

# =============================================================================
# Stage: backend — FastAPI API server
# =============================================================================
FROM base AS backend

RUN $ProgressPreference = 'SilentlyContinue'; \
      & python -m pip install --no-cache-dir --quiet --retries 10 --timeout $env:PIP_DEFAULT_TIMEOUT uv

WORKDIR C:/app

COPY src/backend/pyproject.toml src/backend/.python-version ./
COPY src/backend/app/ ./app/
RUN & python -m uv pip install --system .

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# =============================================================================
# Stage: frontend-build — webpack production build
# =============================================================================
FROM base AS frontend-build

WORKDIR C:/frontend

COPY src/frontend/package.json ./
RUN & 'C:/Program Files/nodejs/npm.cmd' install --no-progress

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
