// =====================================================================
//  Astronaut Cam - ESP32-CAM (AI-Thinker) firmware
//
//  * Live MJPEG video:   http://astronaut.local:81/stream
//  * Control web-app:    http://astronaut.local/   (or the IP printed on Serial)
//  * Pan servo  (torso)  -> GPIO 14
//  * Tilt servo (helmet) -> GPIO 13
//  * Helmet light        -> GPIO 4 (on-board flash LED)
//
//  Board: "AI Thinker ESP32-CAM", Partition: "Huge APP", PSRAM: enabled.
//  Works with arduino-esp32 core 2.x and 3.x.
// =====================================================================
#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_timer.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include "index_html.h"

// ------------------------- user settings -----------------------------
const char *WIFI_SSID = "YOUR_WIFI";        // home network (leave as is to use AP mode only)
const char *WIFI_PASS = "YOUR_PASSWORD";
const char *AP_SSID   = "Astronaut-Cam";    // fallback hotspot
const char *AP_PASS   = "astronaut123";     // at least 8 characters
const char *HOSTNAME  = "astronaut";        // -> http://astronaut.local

#define PAN_PIN   14
#define TILT_PIN  13
#define FLASH_PIN 4

// LEDC channels 2/3 share LEDC timer 1; the camera clock uses channel 0 / timer 0.
#define PAN_CH  2
#define TILT_CH 3

const int  PAN_MIN = 0,   PAN_MAX = 180,  PAN_CENTER = 90;
const int  TILT_MIN = 55, TILT_MAX = 125, TILT_CENTER = 90;   // limited by the visor opening
const bool PAN_INVERT = false, TILT_INVERT = false;
const int  SERVO_MIN_US = 500, SERVO_MAX_US = 2400;
const float SERVO_STEP_DEG = 2.5f;          // max movement per 20 ms tick (smooth motion)
const uint32_t SERVO_RELEASE_MS = 1500;     // stop pulses when idle -> no buzzing / jitter

// ------------------------- AI-Thinker pins ---------------------------
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

// ------------------------- servo control -----------------------------
struct Axis {
  int pin, ch, minDeg, maxDeg;
  bool invert;
  volatile float target;
  float current;
  bool attached;
};

Axis pan  = {PAN_PIN,  PAN_CH,  PAN_MIN,  PAN_MAX,  PAN_INVERT,  PAN_CENTER,  PAN_CENTER,  false};
Axis tilt = {TILT_PIN, TILT_CH, TILT_MIN, TILT_MAX, TILT_INVERT, TILT_CENTER, TILT_CENTER, false};
volatile uint32_t lastMoveMs = 0;
bool flashOn = false;

static void pwmAttach(Axis &a) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttachChannel(a.pin, 50, 14, a.ch);
#else
  ledcSetup(a.ch, 50, 14);
  ledcAttachPin(a.pin, a.ch);
#endif
}

static void pwmWrite(Axis &a, uint32_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(a.pin, duty);
#else
  ledcWrite(a.ch, duty);
#endif
}

static void servoOut(Axis &a) {
  float deg = a.invert ? 180.0f - a.current : a.current;
  uint32_t us = SERVO_MIN_US + (uint32_t)((SERVO_MAX_US - SERVO_MIN_US) * deg / 180.0f);
  pwmWrite(a, us * 16384UL / 20000UL);   // 14-bit duty at 50 Hz (20 ms period)
  a.attached = true;
}

static float clampDeg(const Axis &a, float v) {
  return v < a.minDeg ? a.minDeg : (v > a.maxDeg ? a.maxDeg : v);
}

static void setTarget(Axis &a, float v) {
  a.target = clampDeg(a, v);
  lastMoveMs = millis();
}

// Moves both axes towards their targets in small steps and releases them when idle.
static void servoTask(void *) {
  for (;;) {
    Axis *axes[] = {&pan, &tilt};
    for (Axis *a : axes) {
      float diff = a->target - a->current;
      if (fabsf(diff) > 0.05f) {
        a->current += constrain(diff, -SERVO_STEP_DEG, SERVO_STEP_DEG);
        servoOut(*a);
      } else if (a->attached && millis() - lastMoveMs > SERVO_RELEASE_MS) {
        pwmWrite(*a, 0);
        a->attached = false;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

// ------------------------- HTTP helpers ------------------------------
static bool queryInt(httpd_req_t *req, const char *key, int *out) {
  char query[128], val[16];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK) return false;
  if (httpd_query_key_value(query, key, val, sizeof(val)) != ESP_OK) return false;
  *out = atoi(val);
  return true;
}

static esp_err_t sendStatus(httpd_req_t *req) {
  char json[256];
  sensor_t *s = esp_camera_sensor_get();
  snprintf(json, sizeof(json),
           "{\"pan\":%d,\"tilt\":%d,\"panMin\":%d,\"panMax\":%d,\"tiltMin\":%d,\"tiltMax\":%d,"
           "\"flash\":%d,\"framesize\":%d,\"quality\":%d,\"vflip\":%d,\"hmirror\":%d,\"rssi\":%d}",
           (int)lroundf(pan.target), (int)lroundf(tilt.target), PAN_MIN, PAN_MAX, TILT_MIN, TILT_MAX,
           flashOn, s->status.framesize, s->status.quality, s->status.vflip, s->status.hmirror,
           WiFi.getMode() == WIFI_AP ? 0 : (int)WiFi.RSSI());
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_sendstr(req, json);
}

// ------------------------- HTTP handlers -----------------------------
static esp_err_t indexHandler(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, INDEX_HTML, HTTPD_RESP_USE_STRLEN);
}

// /move?pan=90&tilt=90  (absolute)   /move?dp=5&dt=-5  (relative)   /move?center=1
static esp_err_t moveHandler(httpd_req_t *req) {
  int v;
  if (queryInt(req, "center", &v)) { setTarget(pan, PAN_CENTER); setTarget(tilt, TILT_CENTER); }
  if (queryInt(req, "pan", &v))  setTarget(pan, v);
  if (queryInt(req, "tilt", &v)) setTarget(tilt, v);
  if (queryInt(req, "dp", &v))   setTarget(pan, pan.target + v);
  if (queryInt(req, "dt", &v))   setTarget(tilt, tilt.target + v);
  return sendStatus(req);
}

static esp_err_t statusHandler(httpd_req_t *req) { return sendStatus(req); }

static esp_err_t flashHandler(httpd_req_t *req) {
  int v;
  if (queryInt(req, "on", &v)) {
    flashOn = v != 0;
    digitalWrite(FLASH_PIN, flashOn ? HIGH : LOW);
  }
  return sendStatus(req);
}

// /cam?var=framesize&val=8   (vars: framesize, quality, vflip, hmirror, brightness)
static esp_err_t camHandler(httpd_req_t *req) {
  char query[96], var[24], val[8];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
      httpd_query_key_value(query, "var", var, sizeof(var)) != ESP_OK ||
      httpd_query_key_value(query, "val", val, sizeof(val)) != ESP_OK) {
    httpd_resp_send_404(req);
    return ESP_FAIL;
  }
  int v = atoi(val);
  sensor_t *s = esp_camera_sensor_get();
  if (!strcmp(var, "framesize"))       s->set_framesize(s, (framesize_t)v);
  else if (!strcmp(var, "quality"))    s->set_quality(s, v);
  else if (!strcmp(var, "vflip"))      s->set_vflip(s, v);
  else if (!strcmp(var, "hmirror"))    s->set_hmirror(s, v);
  else if (!strcmp(var, "brightness")) s->set_brightness(s, v);
  return sendStatus(req);
}

static esp_err_t captureHandler(httpd_req_t *req) {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=astronaut.jpg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

#define PART_BOUNDARY "astronautframe"
static const char *STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char *STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static esp_err_t streamHandler(httpd_req_t *req) {
  char partBuf[64];
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  while (res == ESP_OK) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { res = ESP_FAIL; break; }
    size_t hlen = snprintf(partBuf, sizeof(partBuf), STREAM_PART, (unsigned)fb->len);
    res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, partBuf, hlen);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
  }
  return res;
}

static void startServers() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.max_uri_handlers = 8;
  httpd_handle_t ctrl = NULL;
  const httpd_uri_t uris[] = {
    {"/",        HTTP_GET, indexHandler,   NULL},
    {"/move",    HTTP_GET, moveHandler,    NULL},
    {"/status",  HTTP_GET, statusHandler,  NULL},
    {"/flash",   HTTP_GET, flashHandler,   NULL},
    {"/cam",     HTTP_GET, camHandler,     NULL},
    {"/capture", HTTP_GET, captureHandler, NULL},
  };
  if (httpd_start(&ctrl, &config) == ESP_OK)
    for (const httpd_uri_t &u : uris) httpd_register_uri_handler(ctrl, &u);

  // The video stream gets its own server so control requests are never blocked by it.
  config.server_port += 1;   // 81
  config.ctrl_port += 1;
  httpd_handle_t stream = NULL;
  httpd_uri_t streamUri = {"/stream", HTTP_GET, streamHandler, NULL};
  if (httpd_start(&stream, &config) == ESP_OK) httpd_register_uri_handler(stream, &streamUri);
}

// ------------------------- setup -------------------------------------
static bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (psramFound()) {
    c.frame_size = FRAMESIZE_VGA; c.jpeg_quality = 12; c.fb_count = 2; c.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    c.frame_size = FRAMESIZE_QVGA; c.jpeg_quality = 15; c.fb_count = 1; c.fb_location = CAMERA_FB_IN_DRAM;
  }
  if (esp_camera_init(&c) != ESP_OK) return false;
  return true;
}

static void startWiFi() {
  WiFi.setHostname(HOSTNAME);
  if (strcmp(WIFI_SSID, "YOUR_WIFI") != 0) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.printf("Connecting to %s", WIFI_SSID);
    for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(250); Serial.print('.'); }
    Serial.println();
  }
  if (WiFi.status() == WL_CONNECTED) {
    WiFi.setSleep(false);   // lower latency for video + control
    Serial.printf("Open http://%s/  or  http://%s.local/\n", WiFi.localIP().toString().c_str(), HOSTNAME);
  } else {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASS);
    Serial.printf("Hotspot \"%s\" (password %s) -> open http://%s/\n", AP_SSID, AP_PASS,
                  WiFi.softAPIP().toString().c_str());
  }
  if (MDNS.begin(HOSTNAME)) MDNS.addService("http", "tcp", 80);
}

void setup() {
  Serial.begin(115200);
  pinMode(FLASH_PIN, OUTPUT);
  digitalWrite(FLASH_PIN, LOW);

  if (!initCamera()) {
    Serial.println("Camera init failed - check the flex cable and restart");
    delay(3000);
    ESP.restart();
  }

  pwmAttach(pan);
  pwmAttach(tilt);
  servoOut(pan);
  servoOut(tilt);
  lastMoveMs = millis();
  xTaskCreatePinnedToCore(servoTask, "servo", 2048, NULL, 2, NULL, 1);

  startWiFi();
  startServers();
}

void loop() {
  // Reconnect if the home Wi-Fi drops (AP mode needs nothing).
  static uint32_t lastCheck = 0;
  if (WiFi.getMode() == WIFI_STA && millis() - lastCheck > 10000) {
    lastCheck = millis();
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
  }
  delay(100);
}
