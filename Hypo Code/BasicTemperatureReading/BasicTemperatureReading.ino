// ============================================================
// BasicTemperatureReading.ino
//
// Reads temperature from the MAX30205 and IR from the MAX30102,
// then prints both to Serial in labeled format for the Arduino
// Serial Plotter:
//   Temp_C:<value>,IR:<value>
//
// Both sensors share the I2C bus (no address conflict:
//   MAX30205 = 0x48/0x49, MAX30102 = 0x57).
//
// Hardware connections (same for both sensors):
//   SDA - SDA
//   SCL - SCL
//   VIN - 3.3V
//   GND - GND
// ============================================================

#include <Wire.h>
#include "Protocentral_MAX30205.h"
#include "MAX30105.h"

MAX30205 tempSensor;
MAX30105  ppgSensor;

// Match the LED drive and ADC settings used in the main firmware
#define LED_BRIGHTNESS  0xCF   // ~25 mA
#define FINGER_ON       30000  // IR threshold — below this means off-body

void setup() {
  Serial.begin(115200);
  Wire.begin();

  // --- MAX30205 temperature sensor ---
  while (!tempSensor.scanAvailableSensors()) {
    Serial.println("MAX30205 not found — check wiring");
    delay(5000);
  }
  tempSensor.begin();

  // --- MAX30102 PPG sensor ---
  if (!ppgSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("MAX30102 not found — check wiring");
    while (1);
  }
  // brightness, sampleAvg, ledMode, sampleRate, pulseWidth, adcRange
  ppgSensor.setup(LED_BRIGHTNESS, 1, 2, 400, 411, 16384);
}

void loop() {
  // Read temperature
  float temp = tempSensor.getTemperature();

  // Read latest IR sample from PPG FIFO.
  // ir is static so it holds the last valid reading if the FIFO happens
  // to be empty this iteration (avoids spurious zero spikes in the plotter).
  ppgSensor.check();
  static uint32_t ir = 0;
  while (ppgSensor.available()) {
    ir = ppgSensor.getFIFOIR();
    ppgSensor.nextSample();
  }

  // Labeled format — Arduino Serial Plotter renders each label as its own trace
  Serial.print("Temp_C:");
  Serial.print(temp, 2);
  Serial.print(",IR:");
  Serial.println(ir);

  delay(100);  // ~10 Hz
}
