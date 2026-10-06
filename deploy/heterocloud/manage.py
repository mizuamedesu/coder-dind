#!/usr/bin/env python3
"""CLI deployment helper. Run with: uv run --with httpx manage.py ACTION."""
import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / ".heterocloud"
LOCAL.mkdir(mode=0o700, exist_ok=True)
STATE_FILE = LOCAL / "deployment.json"
STATE = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
CONFIG = Path(os.environ.get("HETEROCLOUD_CREDENTIALS_FILE", str(Path.home() / "Library/Application Support/heterocloud/credentials.json")))
CREDS = json.loads(CONFIG.read_text())
ENDPOINT = CREDS["active_endpoint"].rstrip("/")
PROFILE = CREDS["profiles"][CREDS["active_endpoint"]]
ORG = PROFILE["organization_id"]
BASE = ENDPOINT + "/api/v1/organizations/" + ORG
CLIENT = httpx.Client(headers={"Authorization": "Bearer " + PROFILE["access_token"]}, timeout=60)
PROJECT = "019fbdaa-c85e-7950-b0f1-356e78da4b6e"
REGION = "heteronet-global"
CODER_IMAGE = "ghcr.io/coder/coder@sha256:a48f82c2c437e032ce3f6970b4e937a7900cad3c76956956955099465b45e542"


def private_json(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(value, f, indent=2)
        f.write("\n")


def save():
    private_json(STATE_FILE, STATE)


def api(method, path, body=None):
    r = CLIENT.request(method, BASE + path, json=body)
    if not r.is_success:
        # Do not include request bodies, which can contain secrets.
        raise RuntimeError(f"{method} {path}: HTTP {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None


def wait(path, timeout=900):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        obj = api("GET", path)
        phase = obj.get("state")
        if phase != previous:
            print(obj.get("name", path), phase, flush=True)
            previous = phase
        if phase == "ready":
            return obj
        if phase == "error":
            raise RuntimeError(json.dumps(obj.get("status", {})))
        time.sleep(5)
    raise TimeoutError(path)


def ensure(key, path, body):
    if key not in STATE:
        obj = api("POST", path, body)
        STATE[key] = obj["id"]
        save()
    return wait(path + "/" + STATE[key])


def spec(image, cpu, memory, disk, rootfs, port, group):
    return {
        "region": REGION, "image": image, "replicas": 1, "stopped": True,
        "cpu_millis": cpu, "memory_mib": memory,
        "ephemeral_storage_gib": disk, "rootfs_storage_gib": rootfs,
        "ports": [{"name": "http" if port == 7080 else "postgres", "protocol": "tcp", "container_port": port}],
        "exposure": {"type": "internal", "traffic_mode": "forwarded"},
        "egress": {"mode": "internet" if group == "coder" else "disabled", "allow_same_organization": False},
        "network": {"vpc_id": STATE["vpc_id"], "security_groups": [group], "private_name": "coder" if group == "coder" else "postgres"},
        "env": {}, "command": [], "args": [], "metadata": {"application": "coder", "managed_by": "coder-dind"},
    }


def secret(service, name, value):
    api("PUT", f"/flash/services/{service}/secrets/{name}", {"value": value})


def build_bridge():
    binary = LOCAL / "bin/heterocloud-coder"
    binary.parent.mkdir(exist_ok=True)
    subprocess.run(["go", "build", "-trimpath", "-ldflags=-s -w", "-o", str(binary), "."],
                   cwd=Path(__file__).with_name("bridge"),
                   env=dict(os.environ, GOOS="linux", GOARCH="amd64", CGO_ENABLED="0"), check=True)
    return binary


def install_bridge(service, binary):
    raw = binary.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    if STATE.get("bridge_sha256") == checksum:
        return
    encoded = base64.b64encode(gzip.compress(raw)).decode()
    payload = "\n".join(encoded[i:i + 1024] for i in range(0, len(encoded), 1024))
    script = LOCAL / "install-bridge.sh"
    script.write_text("set -eu\numask 077\nmkdir -p /root/.local/bin\n"
                      "base64 -d <<'HC_BRIDGE_BINARY' | gzip -d > /root/.local/bin/heterocloud-coder.next\n"
                      + payload + "\nHC_BRIDGE_BINARY\n"
                      + f"printf '%s  %s\\n' '{checksum}' '/root/.local/bin/heterocloud-coder.next' | sha256sum -c -\n"
                      + "chmod 700 /root/.local/bin/heterocloud-coder.next\n"
                      "mv /root/.local/bin/heterocloud-coder.next /root/.local/bin/heterocloud-coder\n")
    script.chmod(0o600)
    try:
        subprocess.run(["uv", "run", "--with", "httpx", "--with", "websockets",
                        str(Path(__file__).with_name("exec.py")), service, str(script), "--timeout", "300"], check=True)
        STATE["bridge_sha256"] = checksum
        save()
    finally:
        script.unlink(missing_ok=True)


def setup_role():
    if "protected_service_ids" not in STATE:
        STATE["protected_service_ids"] = [o["id"] for o in api("GET", "/flash/services")["items"]]
        save()
    if "task_role_id" not in STATE:
        role = api("POST", "/iam/principals", {"name": "coder-controller"})
        STATE["task_role_id"] = role["id"]
        save()


def bind_role():
    if "task_policy_id" not in STATE:
        protected = STATE["protected_service_ids"] + [STATE["server_id"], STATE["database_id"]]
        policy = api("POST", "/iam/policies", {"name": "Coder workspace provisioner", "document": {
            "version": "2026-07-31", "statements": [
                {"effect": "Allow", "actions": ["flash:CreateInstance", "flash:ListInstances", "flash:GetInstance", "flash:UpdateInstance", "flash:DeleteInstance"], "resources": [f"hc:org:{ORG}:flash/*"]},
                {"effect": "Allow", "actions": ["vpc:AttachSecurityGroup"], "resources": [f"hc:org:{ORG}:vpc/network/{STATE['vpc_id']}/security-group/workspaces"]},
                {"effect": "Deny", "actions": ["flash:GetInstance", "flash:UpdateInstance", "flash:DeleteInstance"], "resources": [f"hc:org:{ORG}:flash/instance/{identifier}" for identifier in protected]},
            ],
        }})
        STATE["task_policy_id"] = policy["id"]
        save()
    if "task_binding_id" not in STATE:
        binding = api("POST", "/iam/bindings", {"principal_id": STATE["task_role_id"], "policy_id": STATE["task_policy_id"]})
        STATE["task_binding_id"] = binding["id"]
        save()


def github_env():
    if not STATE.get("github_login"):
        return {}
    # Registration is closed. The only permitted human account is pre-linked
    # to its immutable GitHub ID; organization membership is not the filter.
    return {
        "CODER_OAUTH2_GITHUB_DEFAULT_PROVIDER_ENABLE": "true",
        "CODER_OAUTH2_GITHUB_ALLOW_EVERYONE": "true",
        "CODER_OAUTH2_GITHUB_ALLOW_SIGNUPS": "false",
        "CODER_DISABLE_PASSWORD_AUTH": "true",
    }


def github_login(username):
    github = json.loads(subprocess.check_output(["gh", "api", "user"], text=True))
    if github["login"].casefold() != username.casefold() or not isinstance(github["id"], int) or github["id"] <= 0:
        raise RuntimeError("The authenticated GitHub account must match the requested account.")
    auth = json.loads((LOCAL / "coder-session.json").read_text())
    coder = httpx.Client(base_url=STATE["url"], headers={"Coder-Session-Token": auth["session_token"]}, timeout=60)
    response = coder.get("/api/v2/users/me")
    response.raise_for_status()
    owner = response.json()
    if not any(role["name"] == "owner" for role in owner["roles"]):
        raise RuntimeError("An existing Coder owner session is required.")
    owner_id = str(uuid.UUID(owner["id"]))
    github_id = str(github["id"])
    token_file = LOCAL / "coder-api-token.json"
    if not token_file.exists():
        response = coder.post("/api/v2/users/me/keys/tokens", json={
            "token_name": "heterocloud-cli", "scopes": ["coder:all"],
            "lifetime": 167 * 60 * 60 * 1_000_000_000,
        })
        response.raise_for_status()
        private_json(token_file, {"session_token": response.json()["key"], "authentication": "api_token"})
    operator = json.loads(token_file.read_text())
    coder.headers["Coder-Session-Token"] = operator["session_token"]
    response = coder.get("/api/v2/users/me")
    response.raise_for_status()
    if response.json()["id"] != owner_id:
        raise RuntimeError("The local operator token belongs to a different Coder user.")
    private_json(LOCAL / "coder-session.json", operator)
    # Coder does not expose an admin API for pre-linking a GitHub ID. Back up
    # the auth tables, then perform the same login-type/link updates locally
    # in the owned database. Never copy a GitHub access token into Coder.
    backup = "/root/postgresql/auth-backups/github-" + str(time.time_ns()) + ".dump"
    script = LOCAL / "configure-github-login.sh"
    script.write_text(f"""set -eu
umask 077
mkdir -p /root/postgresql/auth-backups
pg_dump -U coder -d coder -Fc -t users -t user_links -t api_keys -f '{backup}'
psql -U coder -d coder -X -v ON_ERROR_STOP=1 <<'CODER_GITHUB_POLICY'
BEGIN;
LOCK TABLE users, user_links, api_keys IN SHARE ROW EXCLUSIVE MODE;
DO $policy$
BEGIN
  IF (SELECT count(*) FROM users WHERE NOT deleted AND NOT is_system) != 1
     OR NOT EXISTS (SELECT 1 FROM users WHERE id = '{owner_id}'::uuid
                    AND NOT deleted AND NOT is_system AND 'owner' = ANY(rbac_roles)
                    AND login_type IN ('password', 'github')) THEN
    RAISE EXCEPTION 'Expected exactly the existing human owner; no auth changes applied';
  END IF;
  IF EXISTS (SELECT 1 FROM user_links WHERE user_id = '{owner_id}'::uuid
             AND (login_type != 'github' OR linked_id NOT IN ('', '{github_id}')))
     OR EXISTS (SELECT 1 FROM user_links WHERE linked_id = '{github_id}'
                AND user_id != '{owner_id}'::uuid) THEN
    RAISE EXCEPTION 'A conflicting identity is already linked; no auth changes applied';
  END IF;
END
$policy$;
INSERT INTO user_links (user_id, login_type, linked_id)
VALUES ('{owner_id}'::uuid, 'github', '{github_id}')
ON CONFLICT (user_id, login_type) DO UPDATE SET linked_id = EXCLUDED.linked_id;
UPDATE users SET login_type = 'github', github_com_user_id = {github_id}, updated_at = now()
WHERE id = '{owner_id}'::uuid;
DELETE FROM api_keys WHERE user_id = '{owner_id}'::uuid AND login_type = 'password';
COMMIT;
SELECT u.id, u.username, u.login_type, u.github_com_user_id, l.linked_id
FROM users u JOIN user_links l ON l.user_id = u.id
WHERE u.id = '{owner_id}'::uuid;
CODER_GITHUB_POLICY
""")
    script.chmod(0o600)
    try:
        subprocess.run(["uv", "run", "--with", "httpx", "--with", "websockets",
                        str(Path(__file__).with_name("exec.py")), STATE["database_id"], str(script)], check=True)
    finally:
        script.unlink(missing_ok=True)
    STATE["github_login"] = {"username": github["login"], "github_user_id": github["id"],
                             "coder_user_id": owner_id, "allow_signups": False, "password_auth": False}
    STATE["github_auth_backup"] = backup
    save()
    server = api("GET", "/flash/services/" + STATE["server_id"])
    server["spec"]["env"].update(github_env())
    for name in ("CODER_OAUTH2_GITHUB_ALLOWED_ORGS", "CODER_OAUTH2_GITHUB_ALLOWED_TEAMS"):
        server["spec"]["env"].pop(name, None)
    api("PUT", "/flash/services/" + server["id"], {"name": server["name"], "spec": server["spec"]})
    wait("/flash/services/" + server["id"])
    print("GitHub login restricted to " + github["login"] + "; signups and password login disabled.")


def setup():
    binary = build_bridge()
    setup_role()
    ensure("vpc_id", "/vpc/networks", {
        "project_id": PROJECT, "name": "coder", "spec": {
            "region": REGION, "description": "Coder control plane, private database, and workspaces",
            "nat": {"enabled": True}, "security_groups": ["coder", "database", "workspaces"],
            "rules": [
                {"source": {"type": "security_group", "name": "coder"}, "destination": {"type": "security_group", "name": "database"}, "protocol": "tcp", "port": 5432},
                {"source": {"type": "security_group", "name": "workspaces"}, "destination": {"type": "security_group", "name": "coder"}, "protocol": "tcp", "port": 7080},
            ],
        },
    })
    passwords_file = LOCAL / "admin.json"
    if not passwords_file.exists():
        private_json(passwords_file, {"username": "mizuame", "email": PROFILE["user_email"], "password": secrets.token_urlsafe(24), "postgres_password": secrets.token_urlsafe(32)})
    passwords = json.loads(passwords_file.read_text())
    dbspec = spec("docker.io/library/postgres:17", 250, 512, 8, 1, 5432, "database")
    dbspec["env"] = {"POSTGRES_USER": "coder", "POSTGRES_DB": "coder", "PGDATA": "/root/postgresql/data"}
    dbspec["command"] = ["/bin/sh", "-c"]
    dbspec["args"] = ["chmod 711 /root; exec /usr/local/bin/docker-entrypoint.sh postgres"]
    ensure("database_id", "/flash/services", {"project_id": PROJECT, "name": "coder-postgres", "spec": dbspec})
    secret(STATE["database_id"], "postgres-password", passwords["postgres_password"])
    dbspec["secret_env"] = {"POSTGRES_PASSWORD": "postgres-password"}
    dbspec["stopped"] = False
    api("PUT", "/flash/services/" + STATE["database_id"], {"name": "coder-postgres", "spec": dbspec})
    database = wait("/flash/services/" + STATE["database_id"])
    STATE["database_status"] = database["status"]
    save()
    # Use the provider-returned private DNS name, never a guessed cluster address.
    status = database["status"].get("status", database["status"])
    endpoints = status["private_endpoints"]
    host = endpoints[0].get("host") or endpoints[0].get("hostname") or endpoints[0].get("address")
    if not host:
        raise RuntimeError("Cannot find private database host: " + json.dumps(endpoints))
    main = spec(CODER_IMAGE, 1000, 1024, 8, 2, 7080, "coder")
    main["env"] = {"HOME": "/root", "CODER_HTTP_ADDRESS": "0.0.0.0:7080", "CODER_TELEMETRY_ENABLE": "false", "CODER_PROVISIONER_DAEMONS": "1", "CODER_DERP_FORCE_WEBSOCKETS": "true", "API_DATA_IS_SENSITIVE": "true"}
    main["env"].update(github_env())
    main["command"] = ["/bin/sleep"]
    main["args"] = ["infinity"]
    main["task_role"] = STATE["task_role_id"]
    ensure("server_id", "/flash/services", {"project_id": PROJECT, "name": "coder", "spec": main})
    service = STATE["server_id"]
    STATE["url"] = "https://f-" + service + ".flash.heterocloud.mizuame.app"
    main["env"]["CODER_ACCESS_URL"] = STATE["url"]
    secret(service, "postgres-url", f"postgres://coder:{passwords['postgres_password']}@{host}:5432/coder?sslmode=disable")
    main["secret_env"] = {"CODER_PG_CONNECTION_URL": "postgres-url"}
    main["stopped"] = False
    api("PUT", "/flash/services/" + service, {"name": "coder", "spec": main})
    STATE["authentication"] = "task_iam"
    STATE.pop("credential_expires_at", None)
    save()
    wait("/flash/services/" + service)
    bind_role()
    install_bridge(service, binary)
    main["command"] = ["/root/.local/bin/heterocloud-coder"]
    main["args"] = []
    api("PUT", "/flash/services/" + service, {"name": "coder", "spec": main})
    wait("/flash/services/" + service)
    print("Coder started with internal exposure. Bootstrap its first user before exposing it.")


def expose():
    if not STATE.get("owner_initialized"):
        raise RuntimeError("Run bootstrap before publishing the Coder endpoint.")
    service = STATE["server_id"]
    obj = api("GET", "/flash/services/" + service)
    obj["spec"]["exposure"] = {"type": "public", "endpoint_mode": "web", "traffic_mode": "forwarded"}
    api("PUT", "/flash/services/" + service, {"name": obj["name"], "spec": obj["spec"]})
    wait("/flash/services/" + service)
    print(STATE["url"])


def bootstrap():
    if STATE.get("owner_initialized"):
        print("Owner already initialized")
        return
    admin = json.loads((LOCAL / "admin.json").read_text())
    payload = json.dumps({key: admin[key] for key in ("email", "username", "password")})
    script = LOCAL / "bootstrap.sh"
    contents = ("set -eu\ncurl --fail-with-body --max-time 30 -sS http://127.0.0.1:7080/api/v2/users/first "
                "-H 'Content-Type: application/json' --data-binary @- <<'CODER_FIRST_USER'\n"
                + payload + "\nCODER_FIRST_USER\n")
    with os.fdopen(os.open(script, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(contents)
    try:
        subprocess.run(["uv", "run", "--with", "httpx", "--with", "websockets",
                        str(Path(__file__).with_name("exec.py")), STATE["server_id"], str(script)], check=True)
        STATE["owner_initialized"] = True
        save()
    finally:
        script.unlink(missing_ok=True)


def build_workspace_image():
    """Publish the original Standard Develop image without retaining push credentials."""
    context = ROOT / "templates/standard-develop/image"
    files = sorted(path for path in context.rglob("*") if path.is_file())
    content_hash = hashlib.sha256("".join(hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in files).encode()).hexdigest()[:16]
    local_tag = "coder-standard-develop:" + content_hash
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "-t", local_tag, str(context)], check=True)
    registry = api("GET", "/registry")
    repository = registry["image_prefix"] + "/coder-standard-develop"
    remote_tag = repository + ":" + content_hash
    credential_file = LOCAL / "registry-upload.json"
    if credential_file.exists():
        credential = json.loads(credential_file.read_text())
    else:
        credential = api("POST", "/registry/credentials", {"name": "coder-standard-develop-upload"})
        private_json(credential_file, credential)
    config_dir = LOCAL / "registry-docker-config"
    config_dir.mkdir(mode=0o700, exist_ok=True)
    try:
        host = subprocess.run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                              capture_output=True, text=True, check=True).stdout.strip()
        env = dict(os.environ, DOCKER_CONFIG=str(config_dir), DOCKER_HOST=host)
        env.pop("DOCKER_CONTEXT", None)
        login = subprocess.run(["docker", "login", credential["login_host"], "--username", credential["username"],
                                "--password-stdin"], input=credential["password"] + "\n",
                               capture_output=True, text=True, env=env)
        if login.returncode:
            raise RuntimeError("Flash Registry login failed; credentials were not logged")
        (config_dir / "config.json").chmod(0o600)
        subprocess.run(["docker", "tag", local_tag, remote_tag], check=True)
        subprocess.run(["docker", "push", remote_tag], env=env, check=True)
        inspected = json.loads(subprocess.run(["docker", "image", "inspect", remote_tag],
                                              capture_output=True, text=True, check=True).stdout)[0]
        digest = next(value for value in inspected["RepoDigests"] if value.startswith(repository + "@sha256:"))
        if inspected["Architecture"] != "amd64":
            raise RuntimeError("Workspace image must use Linux amd64")
        STATE["workspace_image"] = digest
        STATE["workspace_image_source"] = "templates/standard-develop/image"
        STATE["workspace_image_context_hash"] = content_hash
        save()
        print("Standard Develop image published:", digest)
    finally:
        api("DELETE", "/registry/credentials/" + credential["credential"]["id"])
        credential_file.unlink(missing_ok=True)
        (config_dir / "config.json").unlink(missing_ok=True)


def publish_template():
    if STATE.get("workspace_image_source") != "templates/standard-develop/image":
        raise RuntimeError("Run build-workspace-image before publishing Standard Develop")
    server = api("GET", "/flash/services/" + STATE["server_id"])
    private_endpoint = server["status"]["status"]["private_endpoints"][0]
    STATE["private_url"] = f"http://{private_endpoint['host']}:{private_endpoint['port']}"
    save()
    values = {
        "organization_id": ORG,
        "project_id": PROJECT,
        "vpc_id": STATE["vpc_id"],
        "workspace_image": STATE["workspace_image"],
        "coder_url": STATE["url"],
        "coder_private_url": STATE["private_url"],
    }
    command = [sys.executable, str(Path(__file__).with_name("coder.py")),
               "templates", "push", STATE.get("template_name", "standard-develop"), "--yes",
               "--directory", str(ROOT / "templates/heterocloud")]
    for key, value in values.items():
        command += ["--variable", f"{key}={value}"]
    subprocess.run(command, check=True)
    template = STATE.get("template_name", "standard-develop")
    subprocess.run([sys.executable, str(Path(__file__).with_name("coder.py")), "templates", "edit", template,
                    "--display-name", "Standard Develop", "--default-ttl", "8h", "--icon", "/icon/code.svg",
                    "--description", "Codex・Claude・gh・tmux・Docker CLI・VS Code・File Browser・Terminal。4CPU/8GiB/30GiB。MDXなし。FlashはDinD未対応。",
                    "--yes"], check=True)
    STATE["template_name"] = template
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "bootstrap", "build-workspace-image", "publish-template", "expose", "github-login", "status"])
    parser.add_argument("--github-user", default="mizuamedesu")
    args = parser.parse_args()
    {"setup": setup, "bootstrap": bootstrap, "build-workspace-image": build_workspace_image,
     "publish-template": publish_template, "expose": expose,
     "github-login": lambda: github_login(args.github_user), "status": lambda: print(json.dumps(STATE, indent=2))}[args.action]()
