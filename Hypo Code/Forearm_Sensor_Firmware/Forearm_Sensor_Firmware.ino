// ============================================================
// Forearm_Sensor_Firmware.ino  —  ESP-NOW receiver + BLE transmitter
// Board: Seeed XIAO ESP32-S3
//
// Receives neck data via ESP-NOW, reads its own sensors (fake),
// batches 10 samples, and streams via BLE notify to the laptop.
//
// BLE packet format (ASCII, semicolon-delimited):
//   "IR_neck,RED_neck,T_neck,IR_arm,RED_arm,T_arm;..."  x BATCH_SIZE
//
// FAKE DATA: arm sensor reads are synthesized sine waves.
// Search for "FAKE DATA" to find the function to replace with
// real MAX30102 + temperature reads.
// ============================================================

#include <WiFi.h>
#include <esp_now.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
// BLE2902 omitted — ESP32 core 3.x adds the CCCD descriptor automatically

// ---- BLE config ----
#define DEVICE_NAME   "PPG_Forearm"
#define SERVICE_UUID  "12345678-1234-1234-1234-123456789abc"
#define CHAR_UUID     "abcdefab-1234-1234-1234-abcdefabcdef"
#define BATCH_SIZE    10
#define BT_LED_PIN     D0


// ---- Timing ----
#define SAMPLE_RATE_HZ      500
#define SAMPLE_INTERVAL_US  (1000000UL / SAMPLE_RATE_HZ)   // 2000 us

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

// ---- Fake arm waveform state ----
static float phase      = 0.0f;
static float noisePhase = 0.0f;
#define HEART_RATE_HZ  1.1f   // slightly different from neck (~66 BPM)

// =============================================================
// FAKE DATA — replace with real sensor reads for integration
// =============================================================
void readArmSensors(float &ir, float &red, float &temp) {
  ir   = 98000.0f + 4800.0f * sinf(phase)
         + 280.0f * sinf(phase * 3.0f)
         + (float)(random(-120, 120));

  red  = 92000.0f + 4200.0f * sinf(phase + 0.15f)
         + 220.0f * sinf(phase * 3.0f + 0.3f)
         + (float)(random(-120, 120));

  // Arm is slightly cooler than neck (relevant for hypovolemia detection)
  temp = 36.2f + 0.04f * sinf(noisePhase);
}
// =============================================================
// END FAKE DATA
// =============================================================

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
  static uint32_t lastUs    = 0;
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

  uint32_t now = micros();
  if (now - lastUs < SAMPLE_INTERVAL_US) return;
  lastUs = now;

  // Advance waveform phases
  phase      += 2.0f * PI * HEART_RATE_HZ / SAMPLE_RATE_HZ;
  noisePhase += 2.0f * PI * 0.05f / SAMPLE_RATE_HZ;
  if (phase      > 2.0f * PI) phase      -= 2.0f * PI;
  if (noisePhase > 2.0f * PI) noisePhase -= 2.0f * PI;

  // Read arm sensors
  Sample s;
  readArmSensors(s.ir_arm, s.red_arm, s.t_arm);

  // Snapshot latest neck data (zeros until first packet arrives)
  s.ir_neck  = neckReceived ? latestNeck.ir          : 0.0f;
  s.red_neck = neckReceived ? latestNeck.red         : 0.0f;
  s.t_neck   = neckReceived ? latestNeck.temperature : 0.0f;

  batchBuf[batchIdx++] = s;

  // When batch is full, notify BLE client
  if (batchIdx >= BATCH_SIZE) {
    batchIdx = 0;
    if (bleConnected) {
      sendBLEBatch();
    }
  }

  // Debug at 10 Hz
  if (++debugTick >= 50) {
    debugTick = 0;
    Serial.printf("[ARM]  IR:%.0f  RED:%.0f  T:%.2fC | [NECK] IR:%.0f  RED:%.0f  T:%.2fC  BLE:%s\n",
                  s.ir_arm,  s.red_arm,  s.t_arm,
                  s.ir_neck, s.red_neck, s.t_neck,
                  bleConnected ? "connected" : "waiting");
  }
}
