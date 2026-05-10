#include <Wire.h>
#include "MAX30105.h"
MAX30105 particleSensor;

#define USEFIFO
/////
// MAKE SURE THAT THE BOARD IS THE XIAO_ESP32C3
/////
// Effective sample rate = 1000Hz / sampleAverage / downsample
// 1000Hz, average=1, skip every other = 500Hz effective
int sampleSkip = 0;  // counter for downsampling

void setup()
{
  Serial.begin(115200);
  Serial.println("Initializing...");

  if (!particleSensor.begin(Wire, I2C_SPEED_FAST))
  {
    Serial.println("MAX30102 was not found. Please check wiring/power/solder jumper at MH-ET LIVE MAX30102 board.");
    while (1);
  }

  byte ledBrightness = 0xCF;
  byte sampleAverage = 1;     // no hardware averaging, we do it in software
  byte ledMode       = 2;     // Red + IR
  int  sampleRate    = 1000;  // 1000Hz from hardware
  int  pulseWidth    = 411;
  int  adcRange      = 16384;
  particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
}

double avered  = 0;
double aveir   = 0;
double sumirrms  = 0;
double sumredrms = 0;
int i   = 0;
int Num = 100;

double ESpO2 = 95.0;
double FSpO2 = 0.85;
double frate = 0.99;

#define TIMETOBOOT  3000
#define SCALE       88.0
#define MAX_SPO2    100.0
#define MIN_SPO2    80.0
#define SAMPLING    2    // plot every 2nd processed sample
#define FINGER_ON   30000
#define MINIMUM_SPO2 80.0

void loop()
{
  uint32_t ir, red;
  double fred, fir;
  double SpO2 = 0;

#ifdef USEFIFO
  particleSensor.check();

  while (particleSensor.available()) {

    red = particleSensor.getFIFORed();
    ir  = particleSensor.getFIFOIR();

    // --- Downsample: skip every other raw sample = 500Hz effective ---
    sampleSkip++;
    if (sampleSkip % 2 != 0) {
      particleSensor.nextSample();
      continue;  // skip this sample
    }

    // Only reaches here on every 2nd sample = 500Hz
    i++;
    fred = (double)red;
    fir  = (double)ir;

    // DC removal via low-pass filter
    avered = avered * frate + fred * (1.0 - frate);
    aveir  = aveir  * frate + fir  * (1.0 - frate);

    // AC component accumulation for SpO2
    sumredrms += (fred - avered) * (fred - avered);
    sumirrms  += (fir  - aveir)  * (fir  - aveir);

    if ((i % SAMPLING) == 0) {
      if (millis() > TIMETOBOOT) {

        float ir_plot  = 2.0 * (aveir  - fir)  / aveir  * SCALE + (MIN_SPO2 + MAX_SPO2) / 2.0;
        float red_plot = 2.0 * (fred - avered) / avered * SCALE + (MIN_SPO2 + MAX_SPO2) / 2.0;

        ir_plot  = constrain(ir_plot,  MIN_SPO2, MAX_SPO2);
        red_plot = constrain(red_plot, MIN_SPO2, MAX_SPO2);

        if (ir < FINGER_ON) ESpO2 = MINIMUM_SPO2;

        Serial.print("IR:");       Serial.print(ir_plot);
        Serial.print(",RED:");     Serial.println(red_plot);
        // Serial.print(",SpO2:");    Serial.print(ESpO2);
        // Serial.print(",Low:");     Serial.print(85.0);
        // Serial.print(",Warning:"); Serial.print(90.0);
        // Serial.print(",Safe:");    Serial.print(95.0);
        // Serial.print(",Max:");     Serial.println(100.0);
      }
    }

    if ((i % Num) == 0) {
      double R = (sqrt(sumredrms) / avered) / (sqrt(sumirrms) / aveir);
      SpO2  = -23.3 * (R - 0.4) + 100.0;
      ESpO2 = FSpO2 * ESpO2 + (1.0 - FSpO2) * SpO2;
      sumredrms = 0.0;
      sumirrms  = 0.0;
      i = 0;
      break;
    }

    particleSensor.nextSample();
  }
#endif
}