# HeteroCloud deployment

HeteroCloud v0.1.102のタスクIAMを使い、Flash上にCoder v2.37.3を展開する。操作はCLI/APIのみ。

## 構成

| サービス | 公開範囲 | リソース | 永続データ |
| --- | --- | --- | --- |
| `coder` | HTTPS Web endpoint | 1 vCPU / 1 GiB RAM / 8 GiB disk | `/root`の設定・キャッシュ・認証接続プログラム |
| `coder-postgres` | VPC内のCoderのみ、TCP 5432 | 0.25 vCPU / 512 MiB RAM / 8 GiB disk | `/root/postgresql/data` |
| ワークスペースごとのFlash | 非公開、Coder Agentから接続 | 4 vCPU / 8 GiB RAM / 30 GiB disk | `/root`、作業用は`/root/projects` |

VPCのNATでCoderとワークスペースの外向き通信を許可する。DBの外向き通信は無効。
停止・再開は`spec.stopped`を更新し、同じFlashサービスIDと永続ホームを保持する。
**ワークスペースを削除すると、対応するFlashサービスとホームも削除される。**

Flashのdiskはイメージ・書き込み用rootfs・永続ホームの合計。rootfsの予約はCoderが2 GiB、DBが1 GiB、ワークスペースが3 GiB。既存PVCを縮小する更新はできない。
Docker-in-DockerはFlash実機で通常のmount namespace作成が`Operation not permitted`になる。ユーザー名前空間経由ではnamespaceとtmpfsを作成できるが、RootlessKitの複数UID/GIDマッピングは`newuidmap: write to uid_map failed: Operation not permitted`で失敗した。このテンプレートではDockerデーモンを起動しない。
`templates/standard-develop/image/Dockerfile`からLinux amd64イメージを作り、Flash Registryへ保存してdigestで固定する。Flashが提供するrootユーザーで実行する。Coder AgentはVPC内のCoderから取得する。
Codex、Claude Code、GitHub CLI、tmux、Docker CLI、code-serverはイメージに同梱する。MDXとAI拡張は含まない。File BrowserはCoderの公式モジュールv1.1.6で導入し、永続`/root`を表示する。Web TerminalとSSHも利用できる。
Codex/Claudeの利用認証は各ワークスペースで行う。

## タスクIAM

Coder本体の`spec.task_role`に専用サービスアカウント`coder-controller`を割り当てる。固定APIキーや個人用CLI tokenをCoderへ渡す必要はない。
ワークスペースにはクラウド操作用のタスクロールを割り当てない。

`bridge/`のGoプログラムがCoderと同じコンテナで起動し、Terraform用の接続口を`127.0.0.1:7081`に設ける。
HeteroCloudが注入する`HETEROCLOUD_ENDPOINT`、`HETEROCLOUD_ORGANIZATION_ID`、`HETEROCLOUD_WORKLOAD_TOKEN_FILE`を使い、Pod JWTを短期API tokenへ交換する。
API tokenはメモリに保持し、有効期限の60秒前を過ぎた次の要求で再取得する。再取得時にはJWTファイルを読み直すため、プラットフォームによるJWT更新も反映される。
クラウドtokenをTerraform変数・state・ログに保存しない。月次のSecret更新は不要。

接続口は同じ組織のFlashサービスAPIと認証確認のみを受け付ける。
IAMポリシーはワークスペース用セキュリティグループへの接続を許可し、展開時点の既存サービスとCoder本体・DBへの取得・更新・削除を拒否する。IAM管理、タスクロールの割り当て、コンテナの管理者exec権限は与えない。
Terraform provisionerを別コンテナへ移す場合は、そのコンテナにもタスクロールと接続プログラムを配置する必要がある。

DBパスワードと接続URLだけをFlash Secret Managerから渡す。
Coder Agent固有tokenは各ワークスペースの環境変数とTerraform stateに含まれる。
`API_DATA_IS_SENSITIVE=true`でREST providerのデータをTerraformログ上の機密値として扱う。

参考: [HeteroCloudタスクIAM](https://github.com/IPA-CyberLab/IPA-RS-HeteroCloud/blob/master/docs/iam/workload-identity.md)。

## ローカル管理情報

リポジトリ直下の`.heterocloud/`はgit対象外、ディレクトリ権限0700。以下のJSONは0600で保存する。

- `deployment.json`: サービスID、URL、イメージdigest、タスクロール・ポリシー・割り当てID。
- `admin.json`: 初期管理者のメール・ユーザー名・パスワードとDBパスワード。
- `coder-session.json`: ローカルCoder CLIセッション。
- `coder-api-token.json`: GitHubログインへの移行後に使う、既存管理者のローカルCLI用API token。
- `verification.json`: 今回の展開に対する実機確認結果。

HeteroCloud CLIの認証情報はローカルの管理操作にのみ使う。`manage.py`はCLIが保存した認証を読み、macOS以外では`HETEROCLOUD_CREDENTIALS_FILE`にcredentials.jsonのパスを指定する。

## 展開

必要なローカルCLI: 認証済みの`heterocloud`、`uv`、`go`、`terraform`、Linux amd64イメージをビルドできるDocker。
展開先のprojectとregionは`manage.py`に記載。Coder CLIは`.heterocloud/bin/coder`へ公式v2.37.3をチェックサム検証して配置する。
Go接続プログラムはLinux amd64向けの静的バイナリとしてビルドし、管理者認証済みのFlash exec APIで永続ホームへ転送する。転送後にもSHA256を検証する。
最初はCoderを非公開で起動し、初期管理者の作成後にHTTPS endpointを公開する。

リポジトリ直下で実行する。

```sh
uv run --with httpx deploy/heterocloud/manage.py setup
uv run --with httpx deploy/heterocloud/manage.py bootstrap
uv run --with httpx deploy/heterocloud/manage.py expose
uv run --with httpx deploy/heterocloud/manage.py build-workspace-image
terraform -chdir=templates/heterocloud init
terraform -chdir=templates/heterocloud validate
uv run --with httpx deploy/heterocloud/manage.py publish-template
uv run --with httpx deploy/heterocloud/coder.py create dev --template standard-develop --yes
```

Coder本体はGHCR上の公式イメージを直接使用する。ワークスペースは標準テンプレートのイメージをFlash Registryへpushする。一時的なpush認証情報はローカルの0600ファイルと専用Docker設定だけに置き、転送後に認証情報を失効させ、ローカルファイルを削除する。クラウドのワークスペースへpush認証情報を渡さない。
ワークスペースの`stopped`/VPC項目を指定するため、`Mastercard/restapi` v3.0.0で公式Flash APIを呼び出す。

既存の`heterocloud`テンプレートは`coder templates edit heterocloud --name standard-develop --yes`で名前を変更し、同じテンプレートIDとワークスペースを保持する。既存ワークスペースへの新しいイメージ・スペックの反映は`coder update dev --use-parameter-defaults`で行う。

## GitHubログインの制限

現在はGitHubアカウント`mizuamedesu`（GitHub ID `97249122`）だけを既存管理者に紐づけ、新規登録とパスワードログインを無効にしている。
Coder上のユーザー名は`mizuame`を維持する。管理者IDと`dev`の所有者は変更しない。
Coder提供のGitHub認証アプリを使うため、別のOAuth client secretは不要。ログイン画面の「Sign in with GitHub」から、GitHubの本人認証を行う。

Coder v2.37.3にはGitHubユーザー名を指定する許可リストがないため、登録を閉じ、唯一の人間ユーザーの`user_links.linked_id`をGitHubの不変IDへ事前に紐づける。
組織による制限を使用しないため`CODER_OAUTH2_GITHUB_ALLOW_EVERYONE=true`とするが、`CODER_OAUTH2_GITHUB_ALLOW_SIGNUPS=false`と既存のID紐づけにより、他のアカウントは登録・ログインできない。
同じメールアドレスで別のGitHub IDが送られても、Coderは既存のIDとの不一致を拒否する。

再設定は、認証済みのGitHub CLIが対象アカウントであることを確認した上で実行する。

```sh
uv run --with httpx deploy/heterocloud/manage.py github-login --github-user mizuamedesu
```

変更前にDBの認証テーブルを`/root/postgresql/auth-backups/`へバックアップする。
対象が唯一の人間の管理者であることと、他のIDが紐づいていないことをトランザクション内で確認してから更新し、旧パスワードセッションを無効にする。
初期管理者パスワードはこの移行後のログインには使えない。
ローカルCLIには同じ管理者のAPI tokenを0600で保存する。有効期間は167時間で、クラウドを操作するタスクIAMとは別の認証。期限後はGitHubでログインし、CoderのAPI tokenを再発行してローカルの認証を更新する。

制限設定、パスワードログイン403、GitHub device authorization 200、管理者とワークスペースの維持を確認した。
本人からGitHub承認後にログインできたとの報告を受けた。確認結果は`.heterocloud/github-login-verification.json`。

参考: [GitHub認証](https://coder.com/docs/admin/users/github-auth)、[v2.37.3のID照合処理](https://github.com/coder/coder/blob/v2.37.3/coderd/userauth.go)。

## 日常操作

2026-10-06にユーザーの依頼で`dev`と検証用`standard-check`、復旧用の一時Flashサービスを削除した。ワークスペースは現在0件で、`standard-develop`テンプレートとCoder本体・DBは稼働している。テンプレートの既定スペックは4 vCPU・8 GiB RAM・30 GiB disk、既定の自動停止は8時間。確認結果は`.heterocloud/standard-template-verification.json`と`.heterocloud/verification.json`。

```sh
uv run --with httpx deploy/heterocloud/coder.py list
uv run --with httpx deploy/heterocloud/coder.py ssh dev -- pwd
uv run --with httpx deploy/heterocloud/coder.py stop dev --yes
uv run --with httpx deploy/heterocloud/coder.py start dev --yes
uv run --with httpx deploy/heterocloud/manage.py status
```

Standard Developではワークスペース1つにつき30 GiBを割り当てる。
2026-10-06に`GET /flash/quota`で保存済みの個別上限を確認した。合計ディスク上限は9,999 GiB。全アカウントの既定値100 GiBではなく、この個別上限が適用される。確認時の割当は`.heterocloud/flash-quota-verification.json`へ保存する。
DBは単一レプリカ。バックアップの保存先は別途設定する。

## 実機確認（2026-10-05）

- 管理者を初期化してから外部公開し、HTTPS health checkと管理者ログインが200。
- 個人用tokenなしで、専用タスクロールの`workload`認証を確認。保護対象サービスの取得は403。
- 初回API tokenの15分の有効期限が過ぎた後、同じPodのまま自動再取得を確認。接続プログラムの交換回数が1から2に増え、ワークスペース取得APIも200。
- タスクIAMで`dev`を作成し、Coder CLIでSSH接続。VS CodeのHTTPS接続は200、アプリ状態は`healthy`。
- 停止時は同じFlashサービスの稼働レプリカが0。再開後は同じIDで1となり、保存ファイルのSHA256が一致。
- 再開後にもCodex 0.160.0、Claude Code 2.1.289、tmux 3.5aを起動。ワークスペースにクラウド認証情報がないことを確認。
- Goの認証キャッシュ・期限後の更新・JWTファイル差し替え・API制限のテスト、Terraform validate、Python/Bash構文検査を通過。

参考: [Flash API](https://github.com/IPA-CyberLab/IPA-RS-HeteroCloud/blob/master/docs/FLASH_API.md)、[VPC](https://github.com/IPA-CyberLab/IPA-RS-HeteroCloud/blob/master/docs/networking/vpc.md)。
