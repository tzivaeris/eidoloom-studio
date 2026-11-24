# Default image for sandboxed folders: common toolchains, so most projects run without choosing an image.
# Studio builds it on first use. A folder can name any other Docker image in its sandbox settings.
FROM debian:trixie-slim

ARG SANDBOX_UID=1000
ARG SANDBOX_GID=1000

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates curl wget git openssh-client less file jq procps unzip zip xz-utils sqlite3 \
      build-essential cmake pkg-config \
      python3 python3-dev python3-pip python3-venv \
      nodejs npm \
      default-jdk-headless maven \
      golang-go \
      rustc cargo \
      ruby ruby-dev bundler \
      php-cli php-mbstring php-xml composer \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g pnpm yarn

# Containers run as the host user, who needs a name and a home for these tools to behave.
RUN echo "sandbox:x:${SANDBOX_UID}:${SANDBOX_GID}::/home/sandbox:/bin/bash" >> /etc/passwd \
 && echo "sandbox:x:${SANDBOX_GID}:" >> /etc/group \
 && mkdir -p /home/sandbox \
 && echo 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/go/bin:$PATH"' > /etc/profile.d/strata-sandbox.sh

ENV PIP_BREAK_SYSTEM_PACKAGES=1
