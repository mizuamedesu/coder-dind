#!/usr/bin/env python3
"""Run the installed Coder CLI without putting its session token in argv."""
import json
import os
from pathlib import Path
import sys
import httpx
from manage import LOCAL, STATE, private_json

auth = LOCAL / "coder-session.json"
if not auth.exists():
    if STATE.get("github_login"):
        operator = LOCAL / "coder-api-token.json"
        if not operator.exists():
            sys.exit("GitHub login is enabled. Restore the local Coder API token or sign in with GitHub to create a new one.")
        private_json(auth, json.loads(operator.read_text()))
    else:
        admin = json.loads((LOCAL / "admin.json").read_text())
        response = httpx.post(STATE["url"] + "/api/v2/users/login",
                              json={"email": admin["email"], "password": admin["password"]}, timeout=30)
        response.raise_for_status()
        private_json(auth, response.json())
session = json.loads(auth.read_text())
env = dict(os.environ, CODER_URL=STATE["url"], CODER_SESSION_TOKEN=session["session_token"],
           CODER_CONFIG_DIR=str(LOCAL / "coder-config"))
binary = str(LOCAL / "bin/coder")
os.execve(binary, [binary, *sys.argv[1:]], env)
