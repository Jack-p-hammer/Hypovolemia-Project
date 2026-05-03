#include <Wire.h>
#include <Protocentral_MAX30205.h>

MAX30205 tempSensor;

void setup() {
  Serial.begin(115200);
  Wire.begin();

  // I2C connection - check connection by seeing if values make sense
  // 1 seconds between each connection
  // If no connection in 5 attempts, freezes and user needs to restart
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

  Serial.println("Temperature sensor connected.");
}

void loop() {
  float temp = tempSensor.getTemperature();

  Serial.print("Temperature: ");
  Serial.print(temp);
  Serial.println(" C");

  delay(1000);
}