// ============================================================
// Forearm_Sensor_Firmware.ino  —  ESP-NOW receiver + BLE transmitter
// Board: Seeed XIAO ESP32-S3
//
// Reads the MAX30102 PPG sensor at 500 Hz, receives neck data
// via ESP-NOW, batches 10 combined samples, and streams via BLE.
//
// BLE packet format (ASCII, semicolon-delimited):
//   "IR_neck,RED_neck,T_neck,IR_arm,RED_arm,T_arm;..."  x BATCH_SIZE
//
// Temperature field transmits 0.0 until a temp sensor is wired up.
// ============================================================

#include <Wire.h>
#include <WiFi.h>
#include <esp_now.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
// BLE2902 omitted — ESP32 core 3.x adds the CCCD descriptor automatically
#include "MAX30105.h"

MAX30105 particleSensor;

// ---- BLE config ----
#define DEVICE_NAME   "PPG_Forearm"
#define SERVICE_UUID  "12345678-1234-1234-1234-123456789abc"
#define CHAR_UUID     "abcdefab-1234-1234-1234-abcdefabcdef"
#define BATCH_SIZE    10
#define BT_LED_PIN     D0


// ---- Timing ----
// MAX30102 hardware runs at 1000 Hz; we skip every other sample → 500 Hz effective.
#define SAMPLE_RATE_HZ  500

// ---- Shared data struct — must match Neck_Sensor_Firmware.ino exactly ----
struct __attribute__((packed)) NeckData {
  float    ir;
  float    red;
  float    temperature;
  uint32_t timestamp_ms;
};

// ---- Latest neck data (written by ESP-NOW callback, read by main loop) ----
static volatile NeckData latestNeck    = {0, 0, 0, 0};
static volatile bool     neckReceived = false;

// ---- BLE state ----
static BLECharacteristic *pChar       = nullptr;
static bool               bleConnected = false;

// ---- Batch buffer ----
struct Sample {
  float ir_neck, red_neck, t_neck;
  float ir_arm,  red_arm,  t_arm;
};
static Sample batchBuf[BATCH_SIZE];
static int    batchIdx = 0;

// ---- Sensor config ----
#define LED_BRIGHTNESS  0x7F    // fixed mid-range brightness (~25 mA)
#define FINGER_ON       30000   // IR threshold to detect finger presence

// ---- Downsampling state ----
static int sampleSkip = 0;

// ---- BLE server callbacks ----
class ServerCB : public BLEServerCallbacks {
  void onConnect(BLEServer *s) {
    bleConnected = true;
    Serial.println("[BLE] Client connected");
  }
  void onDisconnect(BLEServer *s) {
    bleConnected = false;
    Serial.println("[BLE] Client disconnected — restarting advertising");
    BLEDevice::startAdvertising();
  }
};

// ---- ESP-NOW receive callback (runs in WiFi task context) ----
void onDataReceived(const esp_now_recv_info *info, const uint8_t *data, int len) {
  if (len == sizeof(NeckData)) {
    memcpy((void *)&latestNeck, data, sizeof(NeckData));
    neckReceived = true;
  }
}

// ---- Build and send BLE batch notification ----
void sendBLEBatch() {
  // Format: "%.0f,%.0f,%.2f,%.0f,%.0f,%.2f;" per sample
  // Worst case per sample: "999999,999999,99.99,999999,999999,99.99;" = ~43 chars
  // 10 samples = ~430 chars, well within 512-byte MTU payload
  char buf[512];
  int  pos = 0;

  for (int i = 0; i < BATCH_SIZE && pos < (int)sizeof(buf) - 50; i++) {
    pos += snprintf(buf + pos, sizeof(buf) - pos,
                    "%.0f,%.0f,%.2f,%.0f,%.0f,%.2f",
                    batchBuf[i].ir_neck,  batchBuf[i].red_neck, batchBuf[i].t_neck,
                    batchBuf[i].ir_arm,   batchBuf[i].red_arm,  batchBuf[i].t_arm);
    if (i < BATCH_SIZE - 1)
      buf[pos++] = ';';
  }
  buf[pos] = '\0';

  pChar->setValue((uint8_t *)buf, pos);
  pChar->notify();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  
  Serial.println("=== Forearm Sensor Firmware starting ===");

  // LED pin setup
  pinMode(BT_LED_PIN, OUTPUT);
  digitalWrite(BT_LED_PIN, LOW);

  // ---- MAX30102 ----
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("FATAL: MAX30102 not found — check wiring/power/I2C address");
    while (1);
  }
  // Args: brightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange
  particleSensor.setup(LED_BRIGHTNESS, 1, 2, 1000, 411, 16384);
  Serial.println("MAX30102 initialised");

  // ---- ESP-NOW (requires WiFi STA mode) ----
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  Serial.print("Forearm WiFi MAC: ");
  Serial.println(WiFi.macAddress());

  if (esp_now_init() != ESP_OK) {
    Serial.println("FATAL: ESP-NOW init failed");
    while (1);
  }
  esp_now_register_recv_cb(onDataReceived);
  Serial.println("ESP-NOW listening for neck node");

  // ---- BLE ----
  BLEDevice::init(DEVICE_NAME);
  BLEDevice::setMTU(512);

  BLEServer *pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCB());

  BLEService *pService = pServer->createService(SERVICE_UUID);
  pChar = pService->createCharacteristic(CHAR_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  // No need to manually add BLE2902 — added automatically in ESP32 core 3.x
  pService->start();

  BLEAdvertising *pAdv = BLEDevice::getAdvertising();
  pAdv->addServiceUUID(SERVICE_UUID);
  pAdv->setScanResponse(true);
  BLEDevice::startAdvertising();

  Serial.printf("BLE advertising as \"%s\"\n", DEVICE_NAME);
  Serial.println("Forearm sensor firmware ready. Waiting for BLE connection and neck data.");
}

void loop() {
  static uint32_t debugTick = 0;

// LED Logic
  unsigned int blink_interval = 500;
  static unsigned long lastBlink = 0;
  static bool led_state = false;

  if (bleConnected) {
    digitalWrite(BT_LED_PIN, true);
  }
  else {
    if (millis() - lastBlink >= blink_interval) {
      lastBlink = millis();
      led_state = !led_state;
      digitalWrite(BT_LED_PIN, led_state);
    }
  }

  // ---- Read sensor FIFO ----
  particleSensor.check();

  while (particleSensor.available()) {
    uint32_t ir  = particleSensor.getFIFOIR();
    uint32_t red = particleSensor.getFIFORed();

    // Downsample 1000 Hz → 500 Hz
    sampleSkip++;
    if (sampleSkip % 2 != 0) {
      particleSensor.nextSample();
      continue;
    }

    // Build sample
    Sample s;
    s.ir_arm   = (float)ir;
    s.red_arm  = (float)red;
    s.t_arm    = 0.0f;   // populate when temp sensor is added

    // Snapshot latest neck data (zeros until first ESP-NOW packet arrives)
    s.ir_neck  = neckReceived ? latestNeck.ir          : 0.0f;
    s.red_neck = neckReceived ? latestNeck.red         : 0.0f;
    s.t_neck   = neckReceived ? latestNeck.temperature : 0.0f;

    batchBuf[batchIdx++] = s;

    // When batch is full, notify BLE client
    if (batchIdx >= BATCH_SIZE) {
      batchIdx = 0;
      if (bleConnected) sendBLEBatch();
    }

    // Debug at 10 Hz (every 50 samples)
    if (++debugTick >= 50) {
      debugTick = 0;
      bool fingerOn = (ir > FINGER_ON);
      Serial.printf("[ARM] IR:%lu RED:%lu finger:%s | [NECK] IR:%.0f RED:%.0f BLE:%s\n",
                    ir, red, fingerOn ? "YES" : "NO",
                    s.ir_neck, s.red_neck,
                    bleConnected ? "connected" : "waiting");
    }

    particleSensor.nextSample();
  }
}
