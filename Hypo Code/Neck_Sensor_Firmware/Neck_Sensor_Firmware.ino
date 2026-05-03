// ============================================================
// Neck_Sensor_Firmware.ino
// Board: Seeed XIAO ESP32-S3
//
// Generates fake PPG + temperature data at 500 Hz and transmits
// each sample to the forearm node via ESP-NOW.
//
// FAKE DATA: all sensor reads are synthesized sine waves.
// Replace the three functions marked "SENSOR HOOK" below with
// real sensor reads when the hardware is ready.
// ============================================================

#include <WiFi.h>
#include <esp_now.h>

// ---- Forearm node MAC address (WiFi MAC from MAC_Printer sketch) ----
uint8_t forearmMAC[] = {0x90, 0x70, 0x69, 0x0B, 0xE0, 0xA8};

// ---- Shared data struct — must match Forearm_Sensor_Firmware.ino exactly ----
struct __attribute__((packed)) NeckData {
  float    ir;
  float    red;
  float    temperature;
  uint32_t timestamp_ms;
};

// ---- Timing ----
#define SAMPLE_RATE_HZ      500
#define SAMPLE_INTERVAL_US  (1000000UL / SAMPLE_RATE_HZ)   // 2000 us

// ---- Fake waveform state ----
static float phase      = 0.0f;
static float noisePhase = 0.0f;
#define HEART_RATE_HZ  1.2f   // ~72 BPM

// ---- ESP-NOW diagnostics ----
static uint32_t sendOK   = 0;
static uint32_t sendFail = 0;

// ============================================================
// SENSOR HOOKS — replace these three functions with real reads
// ============================================================

// Called once in setup() to initialise any sensors.
void initSensors() {
  // TODO: initialise MAX30102, temp sensor, etc.
}

// Called every sample. Return the current IR and RED ADC counts.
void readPPG(float &ir, float &red) {
  // TODO: replace with real MAX30102 read
  ir  = 100000.0f + 5000.0f * sinf(phase)
        + 300.0f  * sinf(phase * 3.1f)
        + (float)(random(-150, 150));
  red = 95000.0f  + 4500.0f * sinf(phase + 0.12f)
        + 250.0f  * sinf(phase * 3.1f + 0.2f)
        + (float)(random(-150, 150));
}

// Called every sample. Return the current skin temperature in °C.
float readTemperature() {
  // TODO: replace with real temp sensor read
  return 36.8f + 0.05f * sinf(noisePhase);
}

// ============================================================
// END SENSOR HOOKS
// ============================================================

// ---- ESP-NOW send callback ----
void onDataSent(const wifi_tx_info_t *info, esp_now_send_status_t status) {
  if (status == ESP_NOW_SEND_SUCCESS) sendOK++;
  else                                sendFail++;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("=== Neck Sensor Firmware starting ===");

  initSensors();

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  Serial.print("Neck WiFi MAC: ");
  Serial.println(WiFi.macAddress());

  if (esp_now_init() != ESP_OK) {
    Serial.println("FATAL: ESP-NOW init failed");
    while (1);
  }
  esp_now_register_send_cb(onDataSent);

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, forearmMAC, 6);
  peer.channel = 0;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    Serial.println("FATAL: could not add ESP-NOW peer — is forearmMAC filled in?");
    while (1);
  }

  Serial.println("ESP-NOW ready. Streaming fake data at 500 Hz.");
}

void loop() {
  static uint32_t lastUs    = 0;
  static uint32_t debugTick = 0;

  uint32_t now = micros();
  if (now - lastUs < SAMPLE_INTERVAL_US) return;
  lastUs = now;

  // Advance waveform phases
  phase      += 2.0f * PI * HEART_RATE_HZ / SAMPLE_RATE_HZ;
  noisePhase += 2.0f * PI * 0.05f / SAMPLE_RATE_HZ;
  if (phase      > 2.0f * PI) phase      -= 2.0f * PI;
  if (noisePhase > 2.0f * PI) noisePhase -= 2.0f * PI;

  float ir, red;
  readPPG(ir, red);

  NeckData pkt;
  pkt.ir           = ir;
  pkt.red          = red;
  pkt.temperature  = readTemperature();
  pkt.timestamp_ms = millis();
  esp_now_send(forearmMAC, (uint8_t *)&pkt, sizeof(pkt));

  // Debug at 10 Hz (every 50 samples)
  if (++debugTick >= 50) {
    debugTick = 0;
    Serial.printf("IR:%.0f  RED:%.0f  T:%.2f  ok:%lu  fail:%lu\n",
                  pkt.ir, pkt.red, pkt.temperature, sendOK, sendFail);
  }
}
