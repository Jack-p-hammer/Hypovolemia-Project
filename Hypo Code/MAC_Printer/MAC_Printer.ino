// ============================================================
// MAC_Printer.ino
// Flash this to each XIAO ESP32-S3 to get its MAC addresses.
// Copy the WiFi MAC of the FOREARM device into forearmMAC[]
// in Neck_Sensor_Firmware.ino before flashing the neck device.
//
// Note: on ESP32-S3, BLE MAC = WiFi MAC + 2 (last byte).
// This sketch reads the base MAC directly from efuse so it is
// reliable regardless of WiFi stack initialization timing.
// ============================================================

#include <WiFi.h>
#include "esp_efuse.h"
#include "esp_mac.h"

void printMACArray(const uint8_t *mac) {
  for (int i = 0; i < 6; i++) {
    if (mac[i] < 0x10) Serial.print("0");
    Serial.print(mac[i], HEX);
    if (i < 5) Serial.print(":");
  }
}

void printMACHexArray(const uint8_t *mac) {
  // Print in the format used by the firmware array literal: {0xAA, 0xBB, ...}
  Serial.print("{");
  for (int i = 0; i < 6; i++) {
    Serial.print("0x");
    if (mac[i] < 0x10) Serial.print("0");
    Serial.print(mac[i], HEX);
    if (i < 5) Serial.print(", ");
  }
  Serial.print("}");
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println("=== MAC Printer ===");

  // Read base MAC directly from efuse — always reliable
  uint8_t baseMac[6];
  esp_efuse_mac_get_default(baseMac);

  // Derive the other MACs from the base
  uint8_t wifiMac[6], bleMac[6];
  memcpy(wifiMac, baseMac, 6);   // WiFi STA = base
  memcpy(bleMac,  baseMac, 6);
  bleMac[5] += 2;                // BLE = base + 2

  Serial.print("WiFi / ESP-NOW MAC: ");
  printMACArray(wifiMac);
  Serial.println();

  Serial.print("BLE MAC (derived):  ");
  printMACArray(bleMac);
  Serial.println();

  Serial.println();
  Serial.println("--- Paste this into forearmMAC[] in Neck_Sensor_Firmware.ino ---");
  Serial.print("uint8_t forearmMAC[] = ");
  printMACHexArray(wifiMac);
  Serial.println(";");
}

void loop() {}
