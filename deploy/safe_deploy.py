#!/usr/bin/env python3
"""Безпечне розгортання з бокса: bundle main → сервер → ff-merge → рестарт → healthcheck → відкат при збої.

    python deploy/safe_deploy.py <ip>      (ключ: /home/box/.ssh/cyd_lightsail.pem, користувач root)
Запускати ПЕРЕД git push: після успіху HEAD сервера = локальний main, тож таймер cyd-update нічого не перезапускатиме.
Збій → git reset --hard на попередній коміт, рестарт, повторна перевірка; код виходу 1 (не пушити!)."""
import os, subprocess, sys

ip = sys.argv[1]
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "/home/box/.ssh/cyd_lightsail.pem"
SSH = ["-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20"]
dest = f"{os.environ.get('SSH_USER', 'root')}@{ip}"
subprocess.run(["git", "-C", root, "bundle", "create", "/tmp/cyd.bundle", "main"], check=True, capture_output=True)
subprocess.run(["scp", "-q", *SSH, "/tmp/cyd.bundle", f"{dest}:/tmp/cyd.bundle"], check=True)
subprocess.run(["scp", "-q", *SSH, os.path.join(root, "deploy", "healthcheck.py"), f"{dest}:/tmp/cyd_healthcheck.py"], check=True)


def run(cmd):
    r = subprocess.run(["ssh", *SSH, dest, cmd], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


HC = "/opt/cyd-alert/.venv/bin/python /tmp/cyd_healthcheck.py"
rc, old = run("git -C /opt/cyd-alert rev-parse HEAD")
old = old.strip()
rc, out = run("cd /opt/cyd-alert && git fetch -q /tmp/cyd.bundle main && git merge -q --ff-only FETCH_HEAD && git rev-parse HEAD")
print(out.strip())
if rc:
    print("merge failed — nothing deployed"); sys.exit(2)
new = out.strip().splitlines()[-1]
if new == old:
    print("server already at", new)
rc, out = run(f"systemctl restart cyd-alert && {HC}")
print(out.strip())
if rc == 0:
    print(f"DEPLOYED {old[:7]} -> {new[:7]}: healthy"); sys.exit(0)
print(f"UNHEALTHY -> rollback to {old[:7]}")
rc2, out = run(f"cd /opt/cyd-alert && git reset -q --hard {old} && systemctl restart cyd-alert && {HC}")
print(out.strip())
print("ROLLED BACK", "healthy" if rc2 == 0 else "STILL UNHEALTHY")
sys.exit(1)
