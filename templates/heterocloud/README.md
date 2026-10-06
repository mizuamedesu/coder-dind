# Standard Develop on HeteroCloud

Coderワークスペース1つにつき非公開のFlashサービス1つを作成する。
停止・再開では同じサービスと永続`/root`を維持する。削除ではホームも削除される。

Coderでのテンプレート名は`standard-develop`。基本スペックは**4 vCPU / 8 GiB RAM / 30 GiB total disk**、自動停止は既定8時間。

[標準テンプレートのDockerfile](../standard-develop/image/Dockerfile)からLinux amd64イメージを作り、Flash Registryへ保存してdigestで固定する。Codex、Claude Code、GitHub CLI、tmux、Docker CLI、code-serverを同梱する。MDXとAI拡張は含まない。
VS Codeの作業フォルダは`/root/projects`。File Browserは永続`/root`を表示し、データベースを`/root/filebrowser.db`へ保存する。CoderのWeb TerminalとSSHも利用できる。
ディスク30 GiBはイメージ、書き込み用rootfs予約3 GiB、永続ホームの合計。Flashでは`HOME=/root`を使い、各ツールの利用認証もこのホームへ保存する。

元のDocker版にある特権DinDは現在のFlashで実行できないため、この版ではDockerデーモンを起動しない。Docker CLIは利用できる。通常のmount namespace作成は`Operation not permitted`、RootlessKitの複数UID/GIDマッピングも`newuidmap: write to uid_map failed: Operation not permitted`となることを実機で確認した。

Coder本体のタスクIAMでFlashを管理する。クラウドAPIキーや個人用tokenをテンプレート・ワークスペースへ渡す必要はない。
設定とデプロイ操作は[deployment README](../../deploy/heterocloud/README.md)を参照。
