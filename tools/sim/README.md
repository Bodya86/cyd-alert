# cyd_sim — хост-симулятор UI (скриншоти)

Збирає справжній `src/ui.cpp` (LVGL 8.3, 320x240) + settings/outage/analyzer/mapdata зі стабами Arduino/WiFi/NVS
і рендерить сцени в PPM; `render.py` робить PNG 960x720 (nearest), версії в рамці та zip.

    ./build.sh                       # g++ або python3 -m ziglang c++
    ./build/cyd_sim /tmp/shots/raw
    python3 render.py /tmp/shots/raw /tmp/shots

Прошивку не змінює. Потрібна LVGL з `.pio/libdeps/cyd/lvgl` (pio pkg install).
