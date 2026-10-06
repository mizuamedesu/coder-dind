#!/usr/bin/env python3
"""Execute a script in an owned Flash service using its authenticated shell API.

uv run --with httpx --with websockets deploy/heterocloud/exec.py SERVICE SCRIPT
"""
import argparse
import asyncio
import base64
from pathlib import Path
import re
import ssl
import socket
import sys
import time
import uuid
from urllib.parse import quote

import certifi
import websockets
import manage


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("service")
    parser.add_argument("script", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    path = f"/flash/services/{args.service}"
    pods = manage.api("GET", path + "/containers")["items"]
    ready = [p for p in pods if p["ready"] and p["phase"] == "Running"]
    if not ready:
        raise RuntimeError("No ready containers: " + str(pods))
    url = (manage.BASE + path + "/exec?pod=" + quote(ready[0]["name"])).replace("https://", "wss://", 1)
    headers = {"Authorization": "Bearer " + manage.PROFILE["access_token"]}
    async with websockets.connect(url, additional_headers=headers, origin=manage.ENDPOINT,
                                  ssl=ssl.create_default_context(cafile=certifi.where()), family=socket.AF_INET,
                                  open_timeout=20, max_size=16 * 1024 * 1024) as ws:
        await ws.send(b"stty -echo\r")
        until = time.monotonic() + 2
        while time.monotonic() < until:
            try:
                await asyncio.wait_for(ws.recv(), timeout=0.2)
            except asyncio.TimeoutError:
                pass
        encoded = base64.b64encode(args.script.read_bytes()).decode()
        marker = "__FLASH_SCRIPT_DONE__"
        remote = "/tmp/hc-script-" + uuid.uuid4().hex
        delimiter = "HC_SCRIPT_" + uuid.uuid4().hex
        # Keep every PTY line below its canonical input limit, including large
        # binary uploads. Suppress heredoc prompts and keep temporary files private.
        await ws.send(f"umask 077; export PS1='' PS2=''; cat > '{remote}.b64' <<'{delimiter}'\n".encode())
        for offset in range(0, len(encoded), 1024):
            await ws.send((encoded[offset:offset + 1024] + "\n").encode())
        command = (f"{delimiter}\nbase64 -d '{remote}.b64' > '{remote}.sh'; "
                   f"sh '{remote}.sh'; result=$?; rm -f '{remote}.b64' '{remote}.sh'; "
                   f"printf '\\n{marker}:%s\\n' \"$result\"\r")
        await ws.send(command.encode())
        buf = ""
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            msg = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.monotonic()))
            buf += msg.decode(errors="replace") if isinstance(msg, bytes) else msg
            match = re.search(marker + r":(\d+)", buf)
            if match:
                output = buf[:match.start()].replace("\r\n", "\n")
                output = re.sub(r"^(?:~ # )?(?:> )+", "", output)
                print(output, end="")
                return int(match.group(1))
        raise TimeoutError("Remote script timed out")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
