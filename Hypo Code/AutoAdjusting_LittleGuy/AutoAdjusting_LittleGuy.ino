#include <Wire.h>
#include "MAX30105.h"
MAX30105 particleSensor;

#define USEFIFO
/////
// MAKE SURE THAT THE BOARD IS THE XIAO_ESP32C3
/////

// ---- Auto-brightness config ----
#define TARGET_IR_MIN    10000   // much lower threshold to start
#define TARGET_IR_MAX    180000
#define ADJUST_INTERVAL  100     // check more frequently
#define SETTLE_SAMPLES   150     // shorter settle time

#define TARGET_IR_IDEAL   100000  // aim for this
#define BRIGHT_MIN        0x1F    // minimum brightness (~6mA)
#define BRIGHT_MAX        0xFF    // maximum brightness (~50mA)
#define BRIGHT_STEP       15      // how much to change each adjustment
#define PI_THRESHOLD      0.3     // minimum perfusion index % for good signal

// ---- Sample rate config ----
// 1000Hz hardware, skip every other = 500Hz effective
int sampleSkip = 0;

// ---- Brightness state ----
byte ledBrightness   = 0x1F;   // start at minimum, will ramp up
bool calibrated      = false;
int  settleSamples   = 0;       // countdown after a brightness change
int  adjustCounter   = 0;

// ---- Filter state ----
double avered    = 0;
double aveir     = 0;
double sumirrms  = 0;
double sumredrms = 0;
int    i         = 0;
int    Num       = 100;

// ---- SpO2 state ----
double ESpO2 = 95.0;
double FSpO2 = 0.85;
double frate = 0.99;

#define TIMETOBOOT   3000
#define SCALE        88.0
#define MAX_SPO2     100.0
#define MIN_SPO2     80.0
#define SAMPLING     2
#define FINGER_ON    30000
#define MINIMUM_SPO2 80.0

// ---- Forward declarations ----
void adjustBrightness();
float computePI();

void setup()
{
  Serial.begin(115200);
  delay(2000);
  Serial.println("Initializing...");

  if (!particleSensor.begin(Wire, I2C_SPEED_FAST))
  {
    Serial.println("MAX30102 was not found. Check wiring/power/solder jumper.");
    while (1);
  }

  // Start at minimum brightness — will ramp up automatically
  particleSensor.setup(ledBrightness, 1, 2, 1000, 411, 16384);

  Serial.println("Auto-brightness calibration will begin after finger is detected.");
}

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

    // Downsample to 500Hz
    sampleSkip++;
    if (sampleSkip % 2 != 0) {
      particleSensor.nextSample();
      continue;
    }

    // Only reaches here at 500Hz
    i++;
    fred = (double)red;
    fir  = (double)ir;

    // DC removal via low-pass filter
    avered = avered * frate + fred * (1.0 - frate);
    aveir  = aveir  * frate + fir  * (1.0 - frate);

    // AC accumulation
    sumredrms += (fred - avered) * (fred - avered);
    sumirrms  += (fir  - aveir)  * (fir  - aveir);

    // Count down settle period after brightness change
    if (settleSamples > 0) {
      settleSamples--;
    }

    // ---- Auto-brightness adjustment ----
    // Only adjust if:
    //   - finger is detected
    //   - not currently in settle period
    //   - not yet calibrated OR DC drifted out of range
    if (ir > FINGER_ON && settleSamples == 0) {
      adjustCounter++;
      if (adjustCounter >= ADJUST_INTERVAL) {
        adjustCounter = 0;
        adjustBrightness();
      }
    } else if (ir <= FINGER_ON) {
      // Finger removed — reset calibration so it re-tunes when replaced
      ledBrightness = 0x1F;
      calibrated    = false;
      adjustCounter = 0;
      particleSensor.setup(ledBrightness, 1, 2, 1000, 411, 16384);

      // particleSensor.setPulseAmplitudeRed(ledBrightness);
      // particleSensor.setPulseAmplitudeIR(ledBrightness);
    }

    // ---- Serial output ----
    if ((i % SAMPLING) == 0 && millis() > TIMETOBOOT) {

      float ir_plot  = 2.0 * (aveir  - fir)  / aveir  * SCALE + (MIN_SPO2 + MAX_SPO2) / 2.0;
      float red_plot = 2.0 * (fred - avered) / avered * SCALE + (MIN_SPO2 + MAX_SPO2) / 2.0;

      ir_plot  = constrain(ir_plot,  MIN_SPO2, MAX_SPO2);
      red_plot = constrain(red_plot, MIN_SPO2, MAX_SPO2);

      if (ir < FINGER_ON) ESpO2 = MINIMUM_SPO2;

      float pi = computePI();

      Serial.print("IR:");          Serial.print(ir_plot);
      Serial.print(",RED:");        Serial.print(red_plot);
      Serial.print(",PI:");         Serial.print(pi, 2);
      Serial.print(",Brightness:"); Serial.print(ledBrightness);
      Serial.print(",Calibrated:"); Serial.println(calibrated ? 1 : 0);
    }

    // ---- SpO2 recalculation ----
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

// --------------------------------------------------
// Compute perfusion index (AC/DC ratio as %)
// --------------------------------------------------
float computePI() {
  if (aveir <= 0) return 0.0;
  float ac = sqrt(sumirrms / max(i, 1));
  return (ac / aveir) * 100.0;
}

// --------------------------------------------------
// Adjust LED brightness based on DC level and PI
// --------------------------------------------------
void adjustBrightness() {
  float pi = computePI();

  bool dc_too_low  = (aveir < TARGET_IR_MIN);
  bool dc_too_high = (aveir > TARGET_IR_MAX);
  bool pi_good     = (pi >= PI_THRESHOLD);
  bool dc_good     = (!dc_too_low && !dc_too_high);

  // Already in a good state
  if (dc_good && pi_good) {
    if (!calibrated) {
      calibrated = true;
      Serial.print("STATUS:calibrated brightness=0x");
      Serial.print(ledBrightness, HEX);
      Serial.print(" aveir=");
      Serial.print(aveir);
      Serial.print(" PI=");
      Serial.println(pi, 2);
    }
    return;
  }

  // DC too high — decrease brightness
  if (dc_too_high) {
    int newBright = (int)ledBrightness - BRIGHT_STEP;
    if (newBright < BRIGHT_MIN) newBright = BRIGHT_MIN;
    ledBrightness = (byte)newBright;
    calibrated    = false;
    settleSamples = SETTLE_SAMPLES;
    // particleSensor.setPulseAmplitudeRed(ledBrightness);
    // particleSensor.setPulseAmplitudeIR(ledBrightness);
    particleSensor.setup(ledBrightness, 1, 2, 1000, 411, 16384);
    Serial.print("STATUS:brightness_down=0x");
    Serial.println(ledBrightness, HEX);
    return;
  }

  // DC too low or PI too low — increase brightness
  if (dc_too_low || !pi_good) {
    int newBright = (int)ledBrightness + BRIGHT_STEP;
    if (newBright > BRIGHT_MAX) {
      // Already at max and still not good — warn but stop trying
      if (!calibrated) {
        Serial.println("STATUS:max_brightness_reached signal may be poor");
      }
      ledBrightness = BRIGHT_MAX;
      calibrated    = true;  // stop adjusting
      return;
    }
    ledBrightness = (byte)newBright;
    calibrated    = false;
    settleSamples = SETTLE_SAMPLES;
    particleSensor.setup(ledBrightness, 1, 2, 1000, 411, 16384);

    // particleSensor.setPulseAmplitudeRed(ledBrightness);
    // particleSensor.setPulseAmplitudeIR(ledBrightness);
    Serial.print("STATUS:brightness_up=0x");
    Serial.println(ledBrightness, HEX);
    return;
  }
}