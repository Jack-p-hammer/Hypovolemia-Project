// #include <Wire.h>
// #include <Protocentral_MAX30205.h>
// #include <Arduino.h>

// MAX30205 tempSensor;
// bool connected = false; 


// void setup() {
//   pinMode(D7, OUTPUT);
//   pinMode(D8, OUTPUT);
//   Serial.begin(115200);
//   Wire.begin();

//   // I2C connection - check connection by seeing if values make sense
//   // 1 seconds between each connection
//   // If no connection in 5 attempts, freezes and user needs to restart
//   int attempts = 0;
//   float temp_low = 0.0;
//   float temp_high = 50.0;
//   while (true) { // I2C not connected
//     if (attempts >= 5) {
//       Serial.println("MAX30205 failed to connect. Retry.");
//       while (true);
//     }
//     Serial.println("MAX30205 connecting...");

//     float test_temp = tempSensor.getTemperature();
//     if (test_temp >= temp_low && test_temp <= temp_high) break;

//     attempts += 1;
//     delay(1000);
//   }
//   connected = true; 

//   Serial.println("Temperature sensor connected.");
// }

// void loop() {
//   if (connected) {
//      float temp = tempSensor.getTemperature();

//   Serial.print("Temperature: ");
//   Serial.print(temp);
//   Serial.println(" C");


//   }else {
//     Serial.println(" temp sensor not connected");
//   }
//   digitalWrite(D7, HIGH);  // turn the LED on (HIGH is the voltage level)
//   digitalWrite(D8, HIGH);  // turn the LED on (HIGH is the voltage level)
//   delay(1000);
//   digitalWrite(D7, LOW);  // turn the LED on (HIGH is the voltage level)
//   digitalWrite(D8, LOW);  // turn the LED on (HIGH is the voltage level)
//   delay(1000);

// }