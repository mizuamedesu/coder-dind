---
name: mdx-cli
description: MDX 1 cloud infrastructure CLI operations guide. Use when Codex needs to plan, explain, or execute mdx-cli commands for MDX authentication, project selection, VM inventory/deploy/lifecycle/SSH/CSV, network segments, ACL, DNAT, global IP checks, templates, tasks, JSON output, or safe bulk operations.
---

# MDX CLI

## Overview

Use this skill to operate the `mdx` command safely and accurately. The CLI controls real MDX 1 cloud resources, so prefer read-only discovery first, quote VM name patterns, and require explicit user intent before destructive or bulk changes.

For exact command syntax, options, and behavior, read [references/cli-spec.md](references/cli-spec.md).

## Safety Rules

- Confirm the user is on MDX VPN or inside an MDX VM before commands that contact `oprpl.mdx.jp` or `mdxidm.mdx.jp`.
- Use `--json` for read-only discovery when the result will be parsed or filtered.
- Resolve the project explicitly with `--project-id`, `MDX_PROJECT_ID`, or `mdx project select` before project-scoped commands.
- Quote shell patterns such as `"worker-*"` and `"worker-{a-c}-{0-9}"`.
- Before `start`, `stop`, `shutdown`, `reboot`, `reset`, `destroy`, `reconfigure`, ACL/DNAT delete, or `--fix`, show the target set and get explicit user confirmation unless the user already gave that exact instruction.
- Treat `reset`, `destroy`, `network check-ip --fix`, and `network check-acl --fix` as high-risk operations. Do a read-only check first.
- For long tasks, use `--no-wait` only when the user wants asynchronous execution; then give `mdx task status <task-id>` or `mdx task wait <task-id>` as the follow-up.

## Common Workflows

Initial setup:

```bash
mdx auth login
mdx project select
mdx project summary
mdx vm list
```

Inventory and planning:

```bash
mdx project summary --json
mdx template list --json
mdx vm list --json
mdx network segment list --json
mdx network ips --json
```

Deploy VMs:

```bash
mdx vm deploy \
  --template "Ubuntu 22.04" \
  --name "worker-{0-9}" \
  --pack-type cpu \
  --pack-num 3 \
  --disk 40 \
  --service-level spot \
  --key ~/.ssh/id_ed25519.pub \
  --power-on
```

Bulk VM operations:

```bash
mdx vm list --json
mdx vm shutdown "worker-*"
mdx vm destroy "worker-*" --no-wait
```

Network inspection and cleanup:

```bash
mdx network ips
mdx network check-ip
mdx network check-acl
```

## Command Selection

- Use `auth` for login/logout/status.
- Use `project` to list, select, summarize, inspect storage, or list access keys.
- Use `vm` for VM inventory, deploy, lifecycle operations, reconfiguration, SSH, sync, and CSV export.
- Use `network segment` for segment list/show.
- Use `network acl` for interactive ACL list/add/edit/delete.
- Use `network dnat` for interactive DNAT list/add/edit/delete.
- Use `network ips`, `check-ip`, and `check-acl` for global IP and stale rule audits.
- Use `template` for template list/show and to choose deploy parameters.
- Use `task` for operation history and task polling.

## Notes For Execution

- `mdx auth login` is interactive and needs username, password, and OTP. MDX Local Auth is supported; Gakunin is not.
- Credentials and selected project are stored under `~/.config/mdx-cli/`; username/password use keyring and token/project ID are stored by the credential store.
- `--verbose` is a global flag for detailed API logs.
- The CLI auto-refreshes tokens where possible; bulk parallel operations proactively refresh every 30 VMs.
- The default API base URL is `https://oprpl.mdx.jp`.
