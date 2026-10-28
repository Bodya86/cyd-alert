// Дисплей ILI9341 (TFT_eSPI, HSPI) + тач XPT2046 (окрема шина VSPI) + драйвери LVGL
#include "app.h"
#include <SPI.h>
#include <TFT_eSPI.h>
#include <XPT2046_Touchscreen.h>
#include <lvgl.h>

static TFT_eSPI tft;
static SPIClass touchSpi(VSPI);
static XPT2046_Touchscreen ts(PIN_TOUCH_CS, PIN_TOUCH_IRQ);

#define SCR_W 320
#define SCR_H 240
#define BUF_LINES 24
static lv_disp_draw_buf_t s_drawBuf;
static lv_color_t s_buf[SCR_W * BUF_LINES];
static uint32_t s_lastTouch = 0;
#define CH_BL 5

static void flushCb(lv_disp_drv_t* drv, const lv_area_t* a, lv_color_t* px) {
  uint32_t w = a->x2 - a->x1 + 1, h = a->y2 - a->y1 + 1;
  tft.startWrite();
  tft.setAddrWindow(a->x1, a->y1, w, h);
  tft.pushColors((uint16_t*)&px->full, w * h, true);
  tft.endWrite();
  lv_disp_flush_ready(drv);
}

static void touchCb(lv_indev_drv_t*, lv_indev_data_t* data) {
  if (ts.tirqTouched() && ts.touched()) {
    TS_Point p = ts.getPoint();
    if (p.z < 200) { data->state = LV_INDEV_STATE_REL; return; }
    int x = map(p.x, TOUCH_X_MIN, TOUCH_X_MAX, 0, SCR_W - 1);
    int y = map(p.y, TOUCH_Y_MIN, TOUCH_Y_MAX, 0, SCR_H - 1);
    data->point.x = constrain(x, 0, SCR_W - 1);
    data->point.y = constrain(y, 0, SCR_H - 1);
    data->state = LV_INDEV_STATE_PR;
    s_lastTouch = millis();
  } else {
    data->state = LV_INDEV_STATE_REL;
  }
}

void hwSetBrightness(uint8_t v) { ledcWrite(CH_BL, v < 10 ? 10 : v); }

void hwInit() {
  tft.begin();
  tft.setRotation(1);  // альбомна, USB праворуч
  tft.fillScreen(TFT_BLACK);
  ledcSetup(CH_BL, 5000, 8);
  ledcAttachPin(PIN_BACKLIGHT, CH_BL);
  hwSetBrightness(g_set.brightness);

  touchSpi.begin(PIN_TOUCH_CLK, PIN_TOUCH_MISO, PIN_TOUCH_MOSI, PIN_TOUCH_CS);
  ts.begin(touchSpi);
  ts.setRotation(1);

  lv_init();
  lv_disp_draw_buf_init(&s_drawBuf, s_buf, nullptr, SCR_W * BUF_LINES);
  static lv_disp_drv_t disp;
  lv_disp_drv_init(&disp);
  disp.hor_res = SCR_W;
  disp.ver_res = SCR_H;
  disp.flush_cb = flushCb;
  disp.draw_buf = &s_drawBuf;
  lv_disp_drv_register(&disp);

  static lv_indev_drv_t indev;
  lv_indev_drv_init(&indev);
  indev.type = LV_INDEV_TYPE_POINTER;
  indev.read_cb = touchCb;
  lv_indev_drv_register(&indev);
}
