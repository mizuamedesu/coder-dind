FROM ghcr.io/astral-sh/uv:latest AS uv
FROM ubuntu:24.04
COPY --from=uv /uv /uvx /usr/local/bin/
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git gh tmux sudo bash jq unzip xz-utils zstd docker.io locales && apt-get clean
RUN userdel ubuntu; useradd -m -s /bin/bash -u 1000 coder && echo 'coder ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/coder && chmod 440 /etc/sudoers.d/coder
RUN useradd -m -d /opt/tool-installer -s /bin/bash tool-installer
RUN su - tool-installer -c 'curl -fsSL https://chatgpt.com/codex/install.sh | sh'
RUN su - tool-installer -c 'curl -fsSL https://claude.ai/install.sh | bash'
RUN ln -s /opt/tool-installer/.local/bin/codex /usr/local/bin/codex && ln -s /opt/tool-installer/.local/bin/claude /usr/local/bin/claude && if test -e /opt/tool-installer/.local/bin/codex-code-mode-host; then ln -s /opt/tool-installer/.local/bin/codex-code-mode-host /usr/local/bin/codex-code-mode-host; fi && chmod -R a+rX /opt/tool-installer
RUN git clone https://github.com/mizuamedesu/mdx-cli.git /opt/mdx-cli && git -C /opt/mdx-cli checkout 45783dcd50530df47ea22b788bb76aa15a7d9ca2
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_TOOL_DIR=/opt/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv python install 3.13 && uv tool install --python 3.13 /opt/mdx-cli && chmod -R a+rX /opt/uv
COPY mdx-skill /opt/mdx-skill
RUN curl -fsSL https://code-server.dev/install.sh | sh -s -- --method=standalone --prefix=/opt/code-server
USER coder
WORKDIR /home/coder
ENV PATH=/home/coder/.local/bin:/usr/local/bin:/usr/bin:/bin
RUN codex --version && claude --version && mdx --help && tmux -V && gh --version
