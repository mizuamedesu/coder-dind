# Standard Develop

全ユーザー向けの標準開発環境。tmux、GitHub CLI、Codex CLI、Claude Code CLI、Docker CLIを同梱しています。code-server（AI拡張なし）、File BrowserとWeb Terminalを利用できます。

認証は各ユーザーが`codex`、`claude`、`gh auth login`から行ってください。

ホームとDinDデータは停止・再起動後も保持されます。VS Code拡張、Gemini、Copilot、Cursorは含みません。

[HeteroCloud版](../heterocloud/)はこのDockerfileから作ったイメージを使用します。Coderでのテンプレート名は`standard-develop`、基本スペックは4 vCPU・8 GiB RAM・30 GiB total diskです。File BrowserとWeb Terminalも利用できます。現在のFlashでは特権DinDを実行できないため、HeteroCloud版のDocker機能はCLIのみです。
