FROM mcr.microsoft.com/windows/server:ltsc2025

ARG http_proxy
ARG https_proxy
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG INSTALL_NVM=0

ENV http_proxy=${http_proxy} \
      https_proxy=${https_proxy} \
      HTTP_PROXY=${HTTP_PROXY} \
      HTTPS_PROXY=${HTTPS_PROXY} \
      INSTALL_NVM=${INSTALL_NVM}

SHELL ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]

RUN Set-ExecutionPolicy Bypass -Scope Process -Force; \
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; \
    $proxy = if ($env:https_proxy) { $env:https_proxy } elseif ($env:http_proxy) { $env:http_proxy } elseif ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { $env:HTTP_PROXY }; \
    $webClient = New-Object System.Net.WebClient; \
    if ($proxy) { $webClient.Proxy = New-Object System.Net.WebProxy($proxy, $true) }; \
    iex ($webClient.DownloadString('https://community.chocolatey.org/install.ps1'))

RUN choco install -y \
      ffmpeg \
      git \
      nodejs \
      python313 \
      vcredist-all \
      vim

RUN if ($env:INSTALL_NVM -eq '1') { choco install -y nvm.install } else { Write-Host 'Skipping optional nvm.install'; }

RUN & 'C:/Program Files/nodejs/npm.cmd' install -g \
      @nexrender/action-copy@1.49.4 \
      @nexrender/action-encode@1.46.8 \
      @nexrender/cli@1.63.3

WORKDIR C:/workspace
COPY keepalive.ps1 C:/workspace/keepalive.ps1

CMD ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "C:/workspace/keepalive.ps1"]
