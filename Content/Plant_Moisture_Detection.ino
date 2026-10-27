/*
 * =====================================================================================
 * PROJECT 1: PLANT MOISTURE DETECTION SYSTEM
 * Target Microcontroller: STM32 (e.g., STM32F103C8T6 "Blue Pill" / STM32 Nucleo)
 * =====================================================================================
 *
 * HARDWARE CONNECTIONS & PIN ASSIGNMENTS:
 * -------------------------------------------------------------------------------------
 * 1. SOIL MOISTURE SENSOR (Analog):
 *    - VCC (Power)   ---> STM32 3.3V (Recommended for ADC 0-3.3V range safety)
 *    - GND (Ground)  ---> STM32 GND
 *    - AOUT (Signal) ---> STM32 PA0 (ADC Channel 0)
 *
 * 2. 0.96" I2C OLED DISPLAY (SSD1306, 128x64):
 *    - VCC (Power)   ---> STM32 3.3V
 *    - GND (Ground)  ---> STM32 GND
 *    - SCL (Clock)   ---> STM32 PB6 (Default I2C1 SCL pin on Blue Pill / Wire SCL)
 *    - SDA (Data)    ---> STM32 PB7 (Default I2C1 SDA pin on Blue Pill / Wire SDA)
 *    * Note: Default I2C Address is 0x3C (some modules use 0x3D).
 *
 * 3. STATUS / ALERT INDICATOR:
 *    - Onboard LED   ---> STM32 PC13 (Active LOW on standard STM32 Blue Pill boards)
 *    - (Optional Ext LED: Anode to PA1 via 220-330 ohm resistor, Cathode to GND)
 * -------------------------------------------------------------------------------------
 *
 * STM32 DEPENDENCIES REQUIRED:
 * - STM32 Core for Arduino (Official STMicroelectronics:stm32)
 * - Wire.h (Built-in I2C library)
 * - Adafruit_GFX.h (Adafruit Graphics Library)
 * - Adafruit_SSD1306.h (OLED Display Driver)
 * =====================================================================================
 */

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ======================== PIN INITIALIZATION ========================
#define MOISTURE_PIN       PA0    // Analog input pin connected to Moisture Sensor AOUT
#define ALERT_LED_PIN      PC13   // Built-in LED on Blue Pill (active LOW)

// ===================== OLED DISPLAY CONFIGURATION ====================
#define SCREEN_WIDTH       128    // OLED display width in pixels
#define SCREEN_HEIGHT      64     // OLED display height in pixels
#define OLED_RESET         -1     // Reset pin # (or -1 if sharing Arduino reset pin)
#define OLED_I2C_ADDRESS   0x3C   // Typical I2C address for SSD1306 (0x3C or 0x3D)

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ===================== CALIBRATION & THRESHOLDS ======================
// Note: STM32 ADC has a 12-bit resolution by default (0 - 4095).
// Higher analog value = Drier soil (air); Lower analog value = Wet soil (water).
const int AIR_VALUE   = 3700;     // Calibrated ADC reading when sensor is completely dry in air
const int WATER_VALUE = 1300;     // Calibrated ADC reading when sensor is submerged in water

const int DRY_THRESHOLD = 30;     // Below 30% moisture is considered DRY (needs watering)
const int WET_THRESHOLD = 80;     // Above 80% moisture is considered OVERWATERED

// ========================== GLOBAL VARIABLES =========================
int rawAdcValue     = 0;
int moisturePercent = 0;
unsigned long lastUpdateTime = 0;
const unsigned long UPDATE_INTERVAL = 1000; // Update display every 1000ms (1 sec)

// ============================= SETUP =================================
void setup() {
  // Initialize Serial Monitor for debugging
  Serial.begin(115200);
  delay(100);
  Serial.println("\n--- STM32 Plant Moisture Detection System Starting ---");

  // Initialize GPIO pins
  pinMode(MOISTURE_PIN, INPUT_ANALOG);
  pinMode(ALERT_LED_PIN, OUTPUT);
  digitalWrite(ALERT_LED_PIN, HIGH); // Turn LED off initially (Active LOW)

  // Configure STM32 ADC resolution to 12-bit (0 - 4095) if supported
#if defined(STM32_CORE_VERSION) || defined(ARDUINO_ARCH_STM32)
  analogReadResolution(12);
#endif

  // Explicitly configure I2C pins: PB6 for SCL/SCK, PB7 for SDA/SDL
#if defined(STM32_CORE_VERSION) || defined(ARDUINO_ARCH_STM32)
  Wire.setSCL(PB6);
  Wire.setSDA(PB7);
#endif
  Wire.begin();

  // Initialize SSD1306 OLED Display (try default 0x3C, fallback to 0x3D)
  bool oledFound = display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDRESS);
  if (!oledFound) {
    oledFound = display.begin(SSD1306_SWITCHCAPVCC, 0x3D);
  }

  if (!oledFound) {
    Serial.println(F("[ERROR] SSD1306 OLED allocation failed! Check wiring (PB6=SCL, PB7=SDA)."));
    // Blink onboard LED rapidly to indicate hardware fault
    while (true) {
      digitalWrite(ALERT_LED_PIN, LOW);
      delay(150);
      digitalWrite(ALERT_LED_PIN, HIGH);
      delay(150);
    }
  }

  Serial.println(F("[SUCCESS] OLED Initialized Successfully."));

  // Show splash welcome screen
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(18, 15);
  display.println(F("PLANT MONITOR"));
  display.setCursor(26, 30);
  display.println(F("STM32 READY"));
  display.drawRect(10, 48, 108, 6, SSD1306_WHITE);
  display.fillRect(12, 50, 104, 2, SSD1306_WHITE);
  display.display();
  delay(1500);
}

// =========================== MAIN LOOP ===============================
void loop() {
  unsigned long currentMillis = millis();

  if (currentMillis - lastUpdateTime >= UPDATE_INTERVAL) {
    lastUpdateTime = currentMillis;

    // 1. Read Soil Moisture (Take multiple samples to smooth out ADC noise)
    long adcSum = 0;
    const int SAMPLES = 10;
    for (int i = 0; i < SAMPLES; i++) {
      adcSum += analogRead(MOISTURE_PIN);
      delay(5);
    }
    rawAdcValue = adcSum / SAMPLES;

    // 2. Map raw ADC (12-bit: 0-4095) to percentage (0% Dry to 100% Wet)
    moisturePercent = map(rawAdcValue, AIR_VALUE, WATER_VALUE, 0, 100);
    moisturePercent = constrain(moisturePercent, 0, 100);

    // 3. Update Status LED and Serial Log
    String statusText;
    if (moisturePercent < DRY_THRESHOLD) {
      statusText = "NEEDS WATER!";
      digitalWrite(ALERT_LED_PIN, LOW); // LED ON: Alert low moisture
    } else if (moisturePercent > WET_THRESHOLD) {
      statusText = "TOO WET!";
      digitalWrite(ALERT_LED_PIN, HIGH); // LED OFF
    } else {
      statusText = "OPTIMAL";
      digitalWrite(ALERT_LED_PIN, HIGH); // LED OFF: Healthy moisture
    }

    Serial.print(F("Raw ADC: "));
    Serial.print(rawAdcValue);
    Serial.print(F(" | Moisture: "));
    Serial.print(moisturePercent);
    Serial.print(F("% | Status: "));
    Serial.println(statusText);

    // 4. Update OLED Display UI
    renderOLED(moisturePercent, rawAdcValue, statusText);
  }
}

// ======================== OLED DISPLAY ROUTINE =======================
void renderOLED(int percent, int raw, const String& status) {
  display.clearDisplay();

  // Header Title Bar
  display.fillRect(0, 0, 128, 12, SSD1306_WHITE);
  display.setTextColor(SSD1306_BLACK);
  display.setTextSize(1);
  display.setCursor(16, 2);
  display.print(F("PLANT MOISTURE"));

  // Moisture Value (Large Text)
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(2);
  display.setCursor(20, 18);
  display.print(percent);
  display.print(F(" %"));

  // Visual Progress Bar (Width 108px, Height 8px)
  display.drawRect(10, 36, 108, 10, SSD1306_WHITE);
  int barWidth = map(percent, 0, 100, 0, 104);
  display.fillRect(12, 38, barWidth, 6, SSD1306_WHITE);

  // Status message at bottom
  display.setTextSize(1);
  display.setCursor(10, 52);
  display.print(status);

  // Raw ADC debug reading (small on right)
  display.setCursor(85, 52);
  display.print(F("["));
  display.print(raw);
  display.print(F("]"));

  display.display();
}
