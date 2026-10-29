#!/usr/bin/env python3
"""Розгортання з бокса: bundle репозиторію → сервер → provision.sh.  remote.py <ip> [hostname]"""
import os, subprocess, sys, paramiko
ip = sys.argv[1]; host = sys.argv[2] if len(sys.argv) > 2 else f"{ip}.sslip.io"
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
subprocess.run(["git", "-C", root, "bundle", "create", "/tmp/cyd.bundle", "main"], check=True, capture_output=True)
k = paramiko.RSAKey.from_private_key_file(os.path.expanduser("/home/box/.ssh/cyd_lightsail.pem"))
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ip, username=os.environ.get("SSH_USER", "root"), pkey=k, timeout=20)
sf = c.open_sftp(); sf.put("/tmp/cyd.bundle", "/tmp/cyd.bundle"); sf.put(os.path.join(root, "deploy", "provision.sh"), "/tmp/provision.sh"); sf.close()
_, o, _ = c.exec_command(f"sudo bash /tmp/provision.sh {host} 2>&1 | tail -40", get_pty=True)
for line in o: print(line, end="")
print("exit", o.channel.recv_exit_status())
