---
display_name: Docker in Docker
description: Provision Docker-in-Docker workspaces with AI coding tools
icon: ../../../site/static/icon/docker.png
maintainer_github: Till0196
tags: [docker, container, dind]
---

# Docker in Docker ワークスペース

Docker-in-Docker (DinD) 構成の [Coder ワークスペース](https://coder.com/docs/workspaces)テンプレートです。ワークスペース内から Docker コマンドを利用できます。

## Prerequisites

Coder を実行する VM に Docker ソケットが稼働しており、`coder` ユーザーが Docker グループに追加されている必要があります。

```sh
# Add coder user to Docker group
sudo adduser coder docker

# Restart Coder server
sudo systemctl restart coder

# Test Docker
sudo -u coder docker ps
```

## Architecture

このテンプレートは以下のリソースをプロビジョニングします。

- **ワークスペースコンテナ** (`codercom/enterprise-base:ubuntu`) — メインの開発環境
- **DinD コンテナ** (`docker:dind`) — ワークスペース内で Docker を使うためのサイドカー
- **ホームボリューム** (`/home/coder` に永続マウント)
- **DinD ソケットボリューム** — ワークスペースと DinD コンテナ間で Docker ソケットを共有

ワークスペースが再起動しても `/home/coder` 配下のファイルは保持されます。ホームディレクトリ外のツールやファイルは保持されません。

> **Warning**
> DinD コンテナは `privileged: true` で実行されます。これはホストカーネルへのフルアクセスを意味し、コンテナの分離が実質的に無効化されます。信頼できないユーザーやワークロードがある環境では使用しないでください。代替として [Sysbox](https://github.com/nestybox/sysbox) や podman などrootless Docker の利用を検討してください。
> 詳細は [Docker in Workspaces](https://coder.com/docs/admin/templates/extending-templates/docker-in-workspaces) を参照してください。

## プリインストールされるツール

### IDE / エディタ

- **[code-server](https://github.com/coder/code-server)** — ブラウザ上で動作する VS Code (ポート 13337)
- **[JetBrains Gateway](https://registry.coder.com/modules/coder/jetbrains)** — JetBrains IDE リモート接続
- **[File Browser](https://registry.coder.com/modules/coder/filebrowser)** — Web ベースのファイルマネージャ

### AI コーディングツール

| ツール | インストール方法 |
|--------|-----------------|
| [Claude Code](https://claude.ai) | CLI |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | npm (`@google/gemini-cli`) |
| [GitHub Copilot CLI](https://github.com/github/copilot-cli) | npm (`@github/copilot`) |
| [Cursor Agent](https://cursor.com/cli) | CLI |
| [Codex CLI](https://github.com/openai/codex) | GitHub Release |

### code-server 拡張機能

- `anthropic.claude-code`
- `openai.chatgpt`
- `Google.geminicodeassist`
- `MS-CEINTL.vscode-language-pack-ja` (日本語言語パック)

## カスタマイズ

### apt ミラー

デフォルトで日本国内ミラー (`ftp.udx.icscoe.jp`) に切り替わります。海外環境で使用する場合は `startup_script` 内の apt ミラー設定を削除またはコメントアウトしてください。

### Node.js

Node.js 24.x がインストールされます（未インストールの場合のみ）。

### Docker ソケット

`docker_socket` 変数でカスタムの Docker ソケット URI を指定できます（省略時はデフォルトのソケットを使用）。
