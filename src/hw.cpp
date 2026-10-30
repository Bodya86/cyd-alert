// Дисплей ILI9341 (TFT_eSPI, HSPI) + тач XPT2046 (окрема шина VSPI) + драйвери LVGL
#include "app.h"
#include <SPI.h>
#include <TFT_eSPI.h>
#include <XPT2046_Touchscreen.h>
#include <lvgl.h>
#include <esp_heap_caps.h>

static TFT_eSPI tft;
static SPIClass touchSpi(VSPI);
static XPT2046_Touchscreen ts(PIN_TOUCH_CS, PIN_TOUCH_IRQ);

#define SCR_W 320
#define SCR_H 240
#define BUF_LINES 20
// два буфери у внутрішній DRAM (DMA-capable): LVGL малює в один, поки DMA відправляє інший на дисплей
static lv_disp_draw_buf_t s_drawBuf;
static lv_color_t s_buf[SCR_W * BUF_LINES];
static lv_color_t* s_buf2 = nullptr;   // другий буфер — з купи (статичної DRAM не вистачає)
static bool s_dma = false;
static uint32_t s_lastTouch = 0;
// Підсвітка — окремий канал/таймер LEDC. Раніше був канал 5: у arduino-esp32 2.x таймер = (ch/2)%4, тож канал 5
// ділив таймер 2 з бузером (канал 4) — кожен ledcWriteTone() перенастроював частоту/розрядність підсвітки
// (сирена 700–2000 Гц, 10 біт → яскравість падала ~в 4 рази і «пливла») = мерехтіння. Канал 8 — група low-speed,
// таймер 0, більше ніхто його не використовує. 12 кГц — поза видимим/чутним діапазоном і биттям з розгорткою.
#define CH_BL 8
#define BL_FREQ 12000
#define BL_RES 8
#define BL_MIN 12   // нижня межа скважності, щоб на мінімумі не було «стробу»

static void flushCb(lv_disp_drv_t* drv, const lv_area_t* a, lv_color_t* px) {
  uint32_t w = a->x2 - a->x1 + 1, h = a->y2 - a->y1 + 1;
  if (s_dma) {   // транзакція SPI відкрита постійно (шина дисплея окрема від тачу); pushImageDMA сам чекає попередній DMA
    tft.pushImageDMA(a->x1, a->y1, w, h, (uint16_t*)&px->full);
    if (!s_buf2) tft.dmaWait();   // один буфер — не віддаємо його LVGL, поки DMA не дочитав
    lv_disp_flush_ready(drv);
    return;
  }
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

void hwSetBrightness(uint8_t v) {
  static int last = -1;            // пишемо скважність лише при зміні (повзунок шле VALUE_CHANGED на кожен піксель)
  int d = v < BL_MIN ? BL_MIN : v;
  if (d == last) return;
  last = d;
  ledcWrite(CH_BL, d);
}

// ---- акумулятор: дільник на BAT_ADC_PIN; висячий вхід (нічого не підключено) дає шум/0 — тоді індикатор ховається
int hwBatteryMv() {
#if BAT_ADC_PIN >= 0
  if (!g_set.battery) return -1;
  static int mv = -1;
  static float ema = 0;
  static uint32_t at = 0;
  if (at && millis() - at < 5000) return mv;
  at = millis() | 1;
  uint32_t sum = 0, lo = 9999, hi = 0;
  for (int i = 0; i < 16; i++) {
    uint32_t r = analogReadMilliVolts(BAT_ADC_PIN);
    sum += r; if (r < lo) lo = r; if (r > hi) hi = r;
  }
  float v = sum / 16.0f * BAT_DIV * BAT_CAL;
  // валідно: 2.5..4.6 В і стабільно (розкид < 120 мВ на піні)
  if (v < 2500 || v > 4600 || hi - lo > 120) { mv = -1; ema = 0; return mv; }
  ema = ema > 0 ? ema * 0.7f + v * 0.3f : v;
  mv = (int)ema;
  return mv;
#else
  return -1;
#endif
}

int hwBatteryPct(int mv) {
  static const int16_t V[] = {3300, 3450, 3550, 3650, 3700, 3750, 3800, 3850, 3920, 4000, 4100, 4180};
  static const uint8_t P[] = {   0,    5,   10,   20,   30,   40,   50,   60,   70,   80,   90,  100};
  if (mv <= V[0]) return 0;
  for (int i = 1; i < 12; i++)
    if (mv < V[i]) return P[i - 1] + (P[i] - P[i - 1]) * (mv - V[i - 1]) / (V[i] - V[i - 1]);
  return 100;
}

void hwInit() {
  tft.begin();
  tft.setRotation(1);  // альбомна, USB праворуч
  tft.fillScreen(TFT_BLACK);
  ledcSetup(CH_BL, BL_FREQ, BL_RES);
  ledcAttachPin(PIN_BACKLIGHT, CH_BL);
  hwSetBrightness(g_set.brightness);
#if BAT_ADC_PIN >= 0
  analogSetPinAttenuation(BAT_ADC_PIN, ADC_11db);   // до ~3.1 В на піні
#endif

  touchSpi.begin(PIN_TOUCH_CLK, PIN_TOUCH_MISO, PIN_TOUCH_MOSI, PIN_TOUCH_CS);
  ts.begin(touchSpi);
  ts.setRotation(1);

  s_dma = tft.initDMA();
  if (s_dma) {
    tft.setSwapBytes(true); tft.startWrite();
    s_buf2 = (lv_color_t*)heap_caps_malloc(sizeof(lv_color_t) * SCR_W * BUF_LINES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
  }
  Serial.printf("[hw] display DMA %s, %d lines x %s buffer, heap %u\n", s_dma ? "on" : "off", BUF_LINES, s_buf2 ? "double" : "single", ESP.getFreeHeap());

  lv_init();
  lv_disp_draw_buf_init(&s_drawBuf, s_buf, s_buf2, SCR_W * BUF_LINES);
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
