terraform {
  required_providers {
    coder = {
      source = "coder/coder"
    }
    docker = {
      source = "kreuzwerker/docker"
    }
  }
}

locals {
  username = data.coder_workspace_owner.me.name
}

variable "docker_socket" {
  default     = ""
  description = "(Optional) Docker socket URI"
  type        = string
}

provider "docker" {
  # Defaulting to null if the variable is an empty string lets us have an optional variable without having to set our own default
  host = var.docker_socket != "" ? var.docker_socket : null
}

data "coder_provisioner" "me" {}
data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}

resource "coder_agent" "main" {
  arch           = data.coder_provisioner.me.arch
  os             = "linux"
  startup_script = <<-EOT
    set -e

    # Change apt mirror to Japanese mirror
    ARCH=$(dpkg --print-architecture)
    if [ "$ARCH" = "amd64" ]; then
      sudo sed -i.bak 's|http://archive.ubuntu.com/ubuntu/|http://ftp.udx.icscoe.jp/Linux/ubuntu/|g' /etc/apt/sources.list.d/ubuntu.sources
    elif [ "$ARCH" = "arm64" ]; then
      sudo sed -i.bak 's|http://ports.ubuntu.com/ubuntu-ports/|http://ftp.udx.icscoe.jp/Linux/ubuntu-ports/|g' /etc/apt/sources.list.d/ubuntu.sources
    fi

    # Prepare user home with default files on first start.
    if [ ! -f ~/.init_done ]; then
      cp -rT /etc/skel ~
      touch ~/.init_done
    fi

    # Install the latest code-server.
    # Append "--version x.x.x" to install a specific version of code-server.
    mkdir -p $HOME/.cache
    curl -fsSL https://code-server.dev/install.sh | sh -s -- --method=standalone --prefix=$HOME/.cache/code-server

    # Install Japanese language pack & configure locale
    $HOME/.cache/code-server/bin/code-server --install-extension MS-CEINTL.vscode-language-pack-ja
    OUTPUT_DIR="$HOME/.local/share/code-server"
    LANGUAGE_PACK_FOLDER=$(find "$OUTPUT_DIR/extensions" -maxdepth 1 -type d -name "ms-ceintl.vscode-language-pack-*" | head -1)
    if [ -n "$LANGUAGE_PACK_FOLDER" ] && [ -f "$LANGUAGE_PACK_FOLDER/package.json" ] && [ -f "$OUTPUT_DIR/extensions/extensions.json" ]; then
      mkdir -p "$OUTPUT_DIR/User"
      LANGUAGE_PACK_UUID=$(jq -r --arg id "ms-ceintl.$(jq -r .name "$LANGUAGE_PACK_FOLDER/package.json")" \
        '.[] | select(.identifier.id == $id) | .identifier.uuid' "$OUTPUT_DIR/extensions/extensions.json")
      LANGUAGE_PACK_VERSION=$(jq -r .version "$LANGUAGE_PACK_FOLDER/package.json")
      HASH=$(echo -n "$${LANGUAGE_PACK_UUID}$${LANGUAGE_PACK_VERSION}" | md5sum | awk '{print $1}')
      jq -n --arg lp "$LANGUAGE_PACK_FOLDER" --arg hash "$HASH" --arg uuid "$LANGUAGE_PACK_UUID" \
        --slurpfile pkg "$LANGUAGE_PACK_FOLDER/package.json" \
        '($pkg[0].contributes.localizations[0]) as $loc | ($pkg[0].name) as $name |
         (reduce $loc.translations[] as $t ({}; . + {($t.id): "\($lp)/\($t.path)"})) as $tr |
         {($loc.languageId): {hash: $hash, extensions: [{extensionIdentifier: {id: $name, uuid: $uuid}, version: $pkg[0].version}], translations: $tr, label: $loc.localizedLanguageName}}' \
        > "$OUTPUT_DIR/languagepacks.json"
      jq -n --slurpfile pkg "$LANGUAGE_PACK_FOLDER/package.json" \
        '{locale: $pkg[0].contributes.localizations[0].languageId}' > "$OUTPUT_DIR/User/argv.json"
    fi

    # Install code extention
    $HOME/.cache/code-server/bin/code-server \
      --install-extension anthropic.claude-code \
      --install-extension openai.chatgpt \
      --install-extension Google.geminicodeassist

    cat > "$HOME/.local/share/code-server/User/settings.json" <<'SETTINGS'
    {
        "window.autoDetectColorScheme": true,
        "workbench.preferredDarkColorTheme": "Default Dark Modern",
        "workbench.preferredLightColorTheme": "Default Light Modern"
    }
    SETTINGS

    # Start code-server in the background.
    $HOME/.cache/code-server/bin/code-server --auth none --port 13337 --app-name "code-server" > /tmp/code-server.log 2>&1 &

    # Add any commands that should be executed at workspace startup (e.g install requirements, start a program, etc) here

    # Install Node.js
    if ! command -v node &> /dev/null; then
      curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
      sudo apt-get install -y nodejs
    fi

    # --- Claude Code ---
    if ! command -v claude &> /dev/null; then
      curl -fsSL https://claude.ai/install.sh | bash
      export PATH="$HOME/.local/bin:$HOME/.claude/bin:$PATH"
    fi

    # --- Gemini CLI (npm) ---
    sudo npm install -g @google/gemini-cli

    # --- Copilot CLI (npm) ---
    sudo npm install -g @github/copilot

    # --- Cursor Agent ---
    curl -fsSL https://cursor.com/install | bash

    # --- Codex CLI ---
    MACHINE_ARCH=$(uname -m)
    case "$MACHINE_ARCH" in
      aarch64) CODEX_ARCH="aarch64" ;;
      x86_64)  CODEX_ARCH="x86_64" ;;
      *)       echo "Unsupported architecture: $MACHINE_ARCH"; exit 1 ;;
    esac
    cd /tmp
    curl -sSL $(curl -s https://api.github.com/repos/openai/codex/releases/latest | \
      jq -r '.assets[] | select(.name | contains("'"$CODEX_ARCH"'") and endswith("unknown-linux-musl.tar.gz") and (contains("codex-responses-api-proxy") | not)) | .browser_download_url') \
      -o codex-$${CODEX_ARCH}-unknown-linux-musl.tar.gz
    sudo tar -zxvf codex-$${CODEX_ARCH}-unknown-linux-musl.tar.gz -C /usr/local/bin
    sudo mv /usr/local/bin/codex-$${CODEX_ARCH}-unknown-linux-musl /usr/local/bin/codex
    rm codex-$${CODEX_ARCH}-unknown-linux-musl.tar.gz

  EOT

  # These environment variables allow you to make Git commits right away after creating a
  # workspace. Note that they take precedence over configuration defined in ~/.gitconfig!
  # You can remove this block if you'd prefer to configure Git manually or using
  # dotfiles. (see docs/dotfiles.md)
  env = {
    GIT_AUTHOR_NAME     = coalesce(data.coder_workspace_owner.me.full_name, data.coder_workspace_owner.me.name)
    GIT_AUTHOR_EMAIL    = "${data.coder_workspace_owner.me.email}"
    GIT_COMMITTER_NAME  = coalesce(data.coder_workspace_owner.me.full_name, data.coder_workspace_owner.me.name)
    GIT_COMMITTER_EMAIL = "${data.coder_workspace_owner.me.email}"
  }

  # The following metadata blocks are optional. They are used to display
  # information about your workspace in the dashboard. You can remove them
  # if you don't want to display any information.
  # For basic resources, you can use the `coder stat` command.
  # If you need more control, you can write your own script.
  metadata {
    display_name = "CPU Usage"
    key          = "0_cpu_usage"
    script       = "coder stat cpu"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "RAM Usage"
    key          = "1_ram_usage"
    script       = "coder stat mem"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Home Disk"
    key          = "3_home_disk"
    script       = "coder stat disk --path $${HOME}"
    interval     = 60
    timeout      = 1
  }

  metadata {
    display_name = "CPU Usage (Host)"
    key          = "4_cpu_usage_host"
    script       = "coder stat cpu --host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Memory Usage (Host)"
    key          = "5_mem_usage_host"
    script       = "coder stat mem --host"
    interval     = 10
    timeout      = 1
  }

  metadata {
    display_name = "Load Average (Host)"
    key          = "6_load_host"
    # get load avg scaled by number of cores
    script   = <<EOT
      echo "`cat /proc/loadavg | awk '{ print $1 }'` `nproc`" | awk '{ printf "%0.2f", $1/$2 }'
    EOT
    interval = 60
    timeout  = 1
  }

  metadata {
    display_name = "Swap Usage (Host)"
    key          = "7_swap_host"
    script       = <<EOT
      free -b | awk '/^Swap/ { printf("%.1f/%.1f", $3/1024.0/1024.0/1024.0, $2/1024.0/1024.0/1024.0) }'
    EOT
    interval     = 10
    timeout      = 1
  }
}

resource "coder_app" "code-server" {
  count        = data.coder_workspace.me.start_count
  agent_id     = coder_agent.main.id
  slug         = "code-server"
  display_name = "code-server"
  url          = "http://localhost:13337/"
  icon         = "/icon/code.svg"
  subdomain    = false
  share        = "owner"
  order        = 1

  healthcheck {
    url       = "http://localhost:13337/healthz"
    interval  = 5
    threshold = 6
  }
}

# See https://registry.coder.com/modules/coder/jetbrains
module "jetbrains" {
  count      = data.coder_workspace.me.start_count
  source     = "registry.coder.com/coder/jetbrains/coder"
  version    = "~> 1.1"
  agent_id   = coder_agent.main.id
  agent_name = "main"
  folder     = "/home/coder"
  tooltip    = "You need to [install JetBrains Toolbox](https://coder.com/docs/user-guides/workspace-access/jetbrains/toolbox) to use this app."
}

module "filebrowser" {
  source   = "registry.coder.com/modules/filebrowser/coder"
  version  = "~> 1.1.3"
  agent_id = coder_agent.main.id
  agent_name = "main"
  folder   = "/home/coder"
  subdomain  = false
  order      = 2
}

resource "docker_volume" "home_volume" {
  name = "coder-${data.coder_workspace.me.id}-home"
  # Protect the volume from being deleted due to changes in attributes.
  lifecycle {
    ignore_changes = all
  }
  # Add labels in Docker to keep track of orphan resources.
  labels {
    label = "coder.owner"
    value = data.coder_workspace_owner.me.name
  }
  labels {
    label = "coder.owner_id"
    value = data.coder_workspace_owner.me.id
  }
  labels {
    label = "coder.workspace_id"
    value = data.coder_workspace.me.id
  }
  # This field becomes outdated if the workspace is renamed but can
  # be useful for debugging or cleaning out dangling volumes.
  labels {
    label = "coder.workspace_name_at_creation"
    value = data.coder_workspace.me.name
  }
}

resource "docker_volume" "dind_socket" {
  name = "coder-${data.coder_workspace.me.id}-dind-socket"
}

resource "docker_container" "dind" {
  count      = data.coder_workspace.me.start_count
  image      = "docker:dind"
  privileged = true
  name       = "dind-${data.coder_workspace.me.id}"
  entrypoint = ["sh", "-c"]
  command    = ["addgroup -g 1000 coder && rm -f /var/run/docker.pid && exec dockerd -H unix:///var/run/docker.sock --group coder"]
  
  volumes {
    volume_name    = docker_volume.dind_socket.name
    container_path = "/var/run"
    read_only      = false
  }
}

resource "docker_container" "workspace" {
  count = data.coder_workspace.me.start_count
  image = "codercom/enterprise-base:ubuntu"
  # Uses lower() to avoid Docker restriction on container names.
  name = "coder-${data.coder_workspace_owner.me.name}-${lower(data.coder_workspace.me.name)}"
  # Hostname makes the shell more user friendly: coder@my-workspace:~$
  hostname = data.coder_workspace.me.name
  # Use the docker gateway if the access URL is 127.0.0.1
  entrypoint = ["sh", "-c", replace(coder_agent.main.init_script, "/localhost|127\\.0\\.0\\.1/", "host.docker.internal")]

  env = [
    "CODER_AGENT_TOKEN=${coder_agent.main.token}",
    "DOCKER_HOST=unix:///var/run/docker-host/docker.sock"
  ]
  host {
    host = "host.docker.internal"
    ip   = "host-gateway"
  }
  volumes {
    volume_name    = docker_volume.dind_socket.name
    container_path = "/var/run/docker-host"
    read_only      = false
  }
  volumes {
    container_path = "/home/coder"
    volume_name    = docker_volume.home_volume.name
    read_only      = false
  }

  # Add labels in Docker to keep track of orphan resources.
  labels {
    label = "coder.owner"
    value = data.coder_workspace_owner.me.name
  }
  labels {
    label = "coder.owner_id"
    value = data.coder_workspace_owner.me.id
  }
  labels {
    label = "coder.workspace_id"
    value = data.coder_workspace.me.id
  }
  labels {
    label = "coder.workspace_name"
    value = data.coder_workspace.me.name
  }
}
