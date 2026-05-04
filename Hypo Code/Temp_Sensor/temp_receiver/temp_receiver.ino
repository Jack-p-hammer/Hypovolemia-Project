#include <WiFi.h>
#include <esp_now.h>
#include <Wire.h>
#include "Protocentral_MAX30205.h"

MAX30205 tempSensor;

typedef struct {
  float temp;
} message;

message incomingData;

float otherTemp = 0.0;
bool newData = false;


void onReceive(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  memcpy(&incomingData, data, sizeof(incomingData));

  otherTemp = incomingData.temp;
  newData = true;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  tempSensor.begin();

  // temp sensor connect
  int attempts = 0;
  float temp_low = 0.0;
  float temp_high = 50.0;
  while (true) { // I2C not connected
    if (attempts >= 5) {
      Serial.println("MAX30205 failed to connect. Retry.");
      while (true);
    }
    Serial.println("MAX30205 connecting...");

    float test_temp = tempSensor.getTemperature();
    if (test_temp >= temp_low && test_temp <= temp_high) break;

    attempts += 1;
    delay(1000);
  }

  WiFi.mode(WIFI_STA);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW failed");
    while (true) delay(1000);
  }

  esp_now_register_recv_cb(onReceive);

  Serial.println("Neck receiver ready");
}

void loop() {
  // Only run comparison once data has been received
  if (newData) {

    float thisTemp = tempSensor.getTemperature();

    float diff = otherTemp - thisTemp;

    Serial.print("other temp: ");
    Serial.print(otherTemp);
    Serial.println(" C");

    Serial.print("this Temp: ");
    Serial.print(thisTemp);
    Serial.println(" C");

    Serial.print("Difference: ");
    Serial.print(diff);
    Serial.println(" C");
    
    // thresholds I am not sure of the numbers so change accordingly
    float normal = 1.0;
    float mild = 2.0;
    float moderate = 3.0;

    if (diff < normal) {
      Serial.println("Normal");
    }
    else if (diff < mild) {
      Serial.println("Mild Hypovolemia");
    }
    else if (diff < moderate) {
      Serial.println("Moderate Hypovolemia");
    }
    else {
      Serial.println("Severe Hypovolemia");
    }

    Serial.println("----------------------");

    newData = false;
  }

  delay(200);
}