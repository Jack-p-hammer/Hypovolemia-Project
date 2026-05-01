#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <math.h>

#define DEVICE_NAME     "PPG_Sensor"
#define SERVICE_UUID    "12345678-1234-1234-1234-123456789abc"
#define CHAR_UUID       "abcdefab-1234-1234-1234-abcdefabcdef"

#define SAMPLE_RATE_HZ  500
#define HEART_RATE_BPM  65
#define NOISE_LEVEL     0.05
#define BATCH_SIZE      10
#define BT_LED_PIN      D0

BLEServer*         pServer   = nullptr;
BLECharacteristic* pChar     = nullptr;
bool               connected = false;

String batchBuffer = "";
int    batchCount  = 0;

// Fake temperature state
float t_neck = 98.4;
float t_arm  = 95.2;

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* s) override {
    connected = true;
    Serial.println("Client connected");
  }
  void onDisconnect(BLEServer* s) override {
    connected    = false;
    batchBuffer  = "";
    batchCount   = 0;
    Serial.println("Client disconnected — restarting advertising");
    BLEDevice::startAdvertising();
  }
};

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("Starting...");

  // LED pin setup
  pinMode(BT_LED_PIN, OUTPUT);
  digitalWrite(BT_LED_PIN, LOW);

  BLEDevice::init(DEVICE_NAME);
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService* pService = pServer->createService(SERVICE_UUID);
  pChar = pService->createCharacteristic(
    CHAR_UUID,
    BLECharacteristic::PROPERTY_NOTIFY
  );
  pChar->addDescriptor(new BLE2902());
  pService->start();

  BLEAdvertising* pAdv = BLEDevice::getAdvertising();
  pAdv->addServiceUUID(SERVICE_UUID);
  pAdv->setScanResponse(true);
  BLEDevice::startAdvertising();

  Serial.println("Advertising as: " DEVICE_NAME);
}

void loop() {
  static unsigned long lastSample = 0;
  static float t = 0.0;

  unsigned long interval = 1000 / SAMPLE_RATE_HZ;

  unsigned int blink_interval = 500;
  static unsigned long lastBlink = 0;
  static bool led_state = false;

// LED Logic
  if (connected) {
    digitalWrite(BT_LED_PIN, true);
  }
  else {
    if (millis() - lastBlink >= blink_interval) {
      lastBlink = millis();
      led_state = !led_state;
      digitalWrite(BT_LED_PIN, led_state);
    }
  }

  if (millis() - lastSample >= interval) {
    lastSample = millis();

    float freq = HEART_RATE_BPM / 60.0;

    // PPG waveforms
    float neck = 90.0
      + 3.0 * sin(2 * M_PI * freq * t)
      + 1.0 * sin(4 * M_PI * freq * t)
      + 0.3 * sin(6 * M_PI * freq * t)
      + NOISE_LEVEL * ((random(1000) / 500.0) - 1.0);

    float arm = 90.0
      + 2.5 * sin(2 * M_PI * freq * t + 0.1)
      + 0.8 * sin(4 * M_PI * freq * t + 0.1)
      + 0.2 * sin(6 * M_PI * freq * t + 0.1)
      + NOISE_LEVEL * ((random(1000) / 500.0) - 1.0);

    // Slowly drifting fake temperatures
    t_neck += ((random(100) / 500.0) - 0.1) * 0.01;
    t_arm  += ((random(100) / 500.0) - 0.1) * 0.01;
    t_neck  = constrain(t_neck, 97.0, 100.0);
    t_arm   = constrain(t_arm,  93.0,  97.0);

    t += 1.0 / SAMPLE_RATE_HZ;

    // Format: NECK,ARM,T_NECK,T_ARM per sample in batch
    char buf[48];
    snprintf(buf, sizeof(buf), "%.2f,%.2f,%.1f,%.1f;", neck, arm, t_neck, t_arm);
    batchBuffer += buf;
    batchCount++;

    if (batchCount >= BATCH_SIZE) {
      Serial.println(batchBuffer);
      if (connected) {
        pChar->setValue(batchBuffer.c_str());
        pChar->notify();
      }
      batchBuffer = "";
      batchCount  = 0;
    }
  }
}