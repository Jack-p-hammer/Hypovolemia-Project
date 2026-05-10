// ============================================================
// Neck_Sensor_Firmware.ino
// Board: Seeed XIAO ESP32-S3
//
// Reads IR + RED from the MAX30102 PPG sensor and temperature
// from the MAX30205, then transmits each sample to the forearm
// node via ESP-NOW at 500 Hz.
//
// Both sensors share the I2C bus (no address conflict:
//   MAX30102 = 0x57, MAX30205 = 0x48/0x49).
// ============================================================

#include <Wire.h>
#include <WiFi.h>
#include <esp_now.h>
#include "MAX30105.h"
#include "Protocentral_MAX30205.h"

MAX30105 ppgSensor;
MAX30205 tempSensor;

// ---- Forearm node MAC address (WiFi MAC from MAC_Printer sketch) ----
uint8_t forearmMAC[] = {0x1C, 0xDB, 0xD4, 0x5C, 0xA2, 0xBC};

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

// ---- Sensor config (matches Forearm_Sensor_Firmware) ----
#define LED_BRIGHTNESS  0xDF   // ~25 mA

// ---- ESP-NOW status LED ----
#define ESP_LED_PIN  D7
// ---- Latest PPG readings — static so they hold last valid value if FIFO
//      is momentarily empty, avoiding spurious zero spikes ----
static uint32_t irLatest  = 0;
static uint32_t redLatest = 0;

// ---- ESP-NOW diagnostics ----
static uint32_t sendOK     = 0;
static uint32_t sendFail   = 0;
static bool     lastSendOK = false;  // true when the most recent packet was acknowledged

// ---- ESP-NOW send callback ----
void onDataSent(const wifi_tx_info_t *info, esp_now_send_status_t status) {
  if (status == ESP_NOW_SEND_SUCCESS) { sendOK++;   lastSendOK = true;  }
  else                                { sendFail++; lastSendOK = false; }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("=== Neck Sensor Firmware starting ===");

  // LED pin setup — LOW = off on start
  pinMode(ESP_LED_PIN, OUTPUT);
  digitalWrite(ESP_LED_PIN, LOW);

  // ---- MAX30102 (initialises Wire internally) ----
  if (!ppgSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("FATAL: MAX30102 not found — check wiring");
    delay(1000);
  }
  // brightness, sampleAvg, ledMode, sampleRate, pulseWidth, adcRange
  ppgSensor.setup(LED_BRIGHTNESS, 2, 2, 1000, 411, 16384);
  Serial.println("MAX30102 initialised");

  // ---- MAX30205 (shares the Wire bus already started above) ----
  if (!tempSensor.scanAvailableSensors()) {
    Serial.println("MAX30205 not found — check wiring, retrying...");
    delay(5000);
  }
  tempSensor.begin();
  Serial.println("MAX30205 initialised");

  // ---- ESP-NOW ----
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

  Serial.println("ESP-NOW ready. Streaming sensor data at 500 Hz.");
}

void loop() {
  static uint32_t lastUs       = 0;
  static uint32_t debugTick    = 0;

  // LED: solid when last ESP-NOW send was acknowledged, blink while waiting
  static unsigned long lastBlink = 0;
  static bool          ledState  = false;
  if (lastSendOK) {
    digitalWrite(ESP_LED_PIN, HIGH);
  } else {
    if (millis() - lastBlink >= 500) {
      lastBlink = millis();
      ledState  = !ledState;
      digitalWrite(ESP_LED_PIN, ledState);
    }
  }

  // Always drain the PPG FIFO so it never overflows (32-sample limit).
  // Static variables retain the last valid reading between iterations.
  // Always drain the PPG FIFO so it never overflows (32-sample limit).
  // Static variables retain the last valid reading between iterations.
  ppgSensor.check();
  while (ppgSensor.available()) {
    irLatest  = ppgSensor.getFIFOIR();
    redLatest = ppgSensor.getFIFORed();
    ppgSensor.nextSample();
  }

  // Transmit at 500 Hz
  uint32_t now = micros();
  if (now - lastUs < SAMPLE_INTERVAL_US) return;
  lastUs = now;

  // MAX30205 in continuous mode updates every ~125 ms; calling more often
  // just returns the last cached register value — safe and fast.
  float temp = tempSensor.getTemperature();

  NeckData pkt;
  pkt.ir           = (float)irLatest;
  pkt.red          = (float)redLatest;
  pkt.temperature  = temp;
  pkt.timestamp_ms = millis();
  esp_now_send(forearmMAC, (uint8_t *)&pkt, sizeof(pkt));

  // Debug at 10 Hz (every 50 samples)
  if (++debugTick >= 50) {
    debugTick = 0;
    Serial.printf("IR:%.0f  RED:%.0f  T:%.2f  ok:%lu  fail:%lu\n",
                  pkt.ir, pkt.red, pkt.temperature, sendOK, sendFail);
  }
}
