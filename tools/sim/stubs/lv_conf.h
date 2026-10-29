// Simulator wrapper: the firmware's own lv_conf.h + a bigger heap (64-bit pointers make LVGL objects larger)
#ifndef SIM_LV_CONF_WRAPPER_H
#define SIM_LV_CONF_WRAPPER_H
#include "../../../include/lv_conf.h"
#undef LV_MEM_SIZE
#define LV_MEM_SIZE (256U * 1024U)
#endif
