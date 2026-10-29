#!/usr/bin/env python3
"""Пакує зібрані прошивки для релізу GitHub (OTA):

    python tools/fw_release.py --version          → друкує FW_VERSION з include/version.h
    python tools/fw_release.py --out dist [--repo owner/name]

dist/firmware-<env>.bin  — образ застосунку (OTA, слот app0/app1)
dist/cyd-full-<env>.bin  — повний образ для прошивки кабелем з адреси 0x0 (bootloader + розмітка + otadata + app)
dist/version.json        — маніфест: {"version", "built", "env": {<env>: {"url", "size", "md5"}}}
"""
import argparse, datetime, hashlib, json, os, re, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENVS = ["cyd", "cyd2usb"]
PIO = os.path.expanduser("~/.platformio/packages")


def fw_version():
    s = open(os.path.join(ROOT, "include", "version.h"), encoding="utf-8").read()
    return re.search(r'#define\s+FW_VERSION\s+"([^"]+)"', s).group(1)


def merge(env, dst):
    b = os.path.join(ROOT, ".pio", "build", env)
    esptool = os.path.join(PIO, "tool-esptoolpy", "esptool.py")
    boot_app0 = os.path.join(PIO, "framework-arduinoespressif32", "tools", "partitions", "boot_app0.bin")
    subprocess.run([sys.executable, esptool, "--chip", "esp32", "merge_bin", "-o", dst,
                    "--flash_mode", "dio", "--flash_freq", "40m", "--flash_size", "4MB",
                    "0x1000", os.path.join(b, "bootloader.bin"), "0x8000", os.path.join(b, "partitions.bin"),
                    "0xe000", boot_app0, "0x10000", os.path.join(b, "firmware.bin")], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Bodya86/cyd-alert"))
    ap.add_argument("--no-merge", action="store_true")
    a = ap.parse_args()
    ver = fw_version()
    if a.version:
        print(ver)
        return 0
    os.makedirs(a.out, exist_ok=True)
    man = {"version": ver, "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "env": {}}
    for env in ENVS:
        src = os.path.join(ROOT, ".pio", "build", env, "firmware.bin")
        if not os.path.exists(src):
            print("немає збірки", env, file=sys.stderr)
            continue
        name = f"firmware-{env}.bin"
        shutil.copy(src, os.path.join(a.out, name))
        data = open(src, "rb").read()
        man["env"][env] = {"url": f"https://github.com/{a.repo}/releases/download/v{ver}/{name}",
                           "size": len(data), "md5": hashlib.md5(data).hexdigest()}
        if not a.no_merge:
            merge(env, os.path.join(a.out, f"cyd-full-{env}.bin"))
    if not man["env"]:
        return 1
    with open(os.path.join(a.out, "version.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps(man, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
