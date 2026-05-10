#include "i2c_scanner.h"

void i2csetup() {
  Wire.begin();
}

void i2cloop(Stream& debug) {
  byte error, address;
  int nDevices = 0;

  debug.println("Scanning...");

  for (address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    error = Wire.endTransmission();

    if (error == 0) {
      debug.print("I2C device found at address 0x");
      if (address < 16) debug.print("0");
      debug.println(address, HEX);
      nDevices++;
    } else if (error == 4) {
      debug.print("Unknown error at address 0x");
      if (address < 16) debug.print("0");
      debug.println(address, HEX);
    }
  }

  if (nDevices == 0) debug.println("No I2C devices found");
  else debug.println("done");
}
