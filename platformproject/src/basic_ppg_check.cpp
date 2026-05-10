// #include <Arduino.h>
// #include <Wire.h>
// #include "MAX30105.h"
// #include "i2c_scanner.h"

// MAX30105 particleSensor;
// #define debug Serial

// void setup() {
//   debug.begin(115200);
//   pinMode(D7, OUTPUT);
//   pinMode(D8, OUTPUT);

//   debug.println("MAX30105 Basic Readings Example");

//   if (!particleSensor.begin()) {
//     debug.println("MAX30105 was not found. Please check wiring/power.");
//     while (1);
//   }

//   particleSensor.setup();
//   i2csetup();
// }

// void loop() {
//   debug.print(" R[");
//   debug.print(particleSensor.getRed());
//   debug.print("] IR[");
//   debug.print(particleSensor.getIR());
//   debug.print("] G[");
//   debug.print(particleSensor.getGreen());
//   debug.println("]");

//   i2cloop(debug);

//   digitalWrite(D7, HIGH);
//   digitalWrite(D8, HIGH);
//   delay(1000);
//   digitalWrite(D7, LOW);
//   digitalWrite(D8, LOW);
//   delay(1000);
// }
