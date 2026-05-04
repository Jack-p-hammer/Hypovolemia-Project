#include <WiFi.h>
#include <esp_now.h>
#include <Wire.h>
#include "Protocentral_MAX30205.h"

MAX30205 tempSensor;

typedef struct {
  float temp;
} message;

message msg;

uint8_t receiverMAC[] = {0x00,0x00,0x00,0x00,0x00,0x00}; // I don't know receiver MAC address

void setup() {
  Serial.begin(115200);
  Wire.begin();

  tempSensor.begin();
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
    while(true) delay(1000);
  }

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, receiverMAC, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;

  esp_now_add_peer(&peerInfo);
}

void loop() {
  msg.temp = tempSensor.getTemperature();

  esp_now_send(receiverMAC, (uint8_t *)&msg, sizeof(msg));

  Serial.print("Sent Temp: ");
  Serial.println(msg.temp);

  delay(1000);
}