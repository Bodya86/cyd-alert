# Firmware ↔ server analyzer parity

```
cd tools/fw_parity
PATH=/tmp/zbin:$PATH g++ -std=gnu++17 -O2 -I../../test/host -I../../include -o parity parity_main.cpp ../../src/analyzer.cpp ../../src/textutil.cpp
python3 parity.py [raw_dir]     # ~6 min, writes parity_result.json
```
Every unique archive text × 5 configs (home city, local oblast, remote, district 0, district 3) × {truncated to 359 bytes as on device, full}.
Audit 30.10 (FW 1.3.18): truncated-input level mismatches 191 → 0; full-text mismatches are by design (TG_TEXT_MAX).
