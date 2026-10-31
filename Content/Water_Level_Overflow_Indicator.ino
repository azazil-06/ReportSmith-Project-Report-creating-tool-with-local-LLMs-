/*
 * PROJECT: Water Level Detection using STM32 and Ultrasonic Sensor
 * COURSE: Microcontrollers (PBCST504) - V Semester B.Tech
 * 
 * TEAM MEMBERS:
 *   - DEVANANDA.S (SBC24CS097)
 *   - NEEHARA ANIL K K (SBC24CS154)
 *   - SANU R GEEVARGHESE (SBC24CS180)
 *   - MADHAV PADMAKUMAR (SBC24CS144)
 * 
 * GUIDE: Ms. Aryalekshmi A, Assistant Professor
 * 
 * HARDWARE SETUP:
 *   - MCU: STM32F103C8T6 (Blue Pill, ARM Cortex-M3 @ 72 MHz)
 *   - Sensor: HC-SR04 Ultrasonic Distance Sensor
 *       TRIG -> PB0 (GPIO Output)
 *       ECHO -> PB1 (GPIO Input)
 *   - Actuator: Active Buzzer Module -> PA1 (GPIO Output)
 *   - Visual Indicators:
 *       RED LED (Overflow Alert) -> PA2 (GPIO Output)
 *       GREEN LED (Normal Level) -> PA3 (GPIO Output)
 *       Onboard Status LED      -> PC13 (Active-Low GPIO)
 *   - Display: 16x2 LCD via I2C (PCF8574 backpack at 0x27)
 *       SCL -> PB6 (I2C1_SCL)
 *       SDA -> PB7 (I2C1_SDA)
 *   - Power Supply: LM2596 DC-DC Buck Converter (9V/12V Battery down to 5V DC)
 *   - Telemetry: Serial USART1 (PA9 TX, PA10 RX) at 115200 baud
 */

#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// --- PIN DEFINITIONS ---
#define TRIG_PIN PB0        // HC-SR04 Ultrasonic Trigger Pin
#define ECHO_PIN PB1        // HC-SR04 Ultrasonic Echo Pin
#define BUZZER_PIN PA1      // Active Buzzer Audible Alarm Driver
#define LED_RED_PIN PA2     // Red Alert LED (Overflow Warning Indicator)
#define LED_GREEN_PIN PA3   // Green Status LED (Normal / Safe Water Level)
#define LCD_SCL_PIN PB6     // 16x2 LCD I2C SCL Clock Pin
#define LCD_SDA_PIN PB7     // 16x2 LCD I2C SDA Data Pin
#define STATUS_LED_PIN PC13 // Onboard Diagnostic LED (Active-Low)

// --- OPERATIONAL CONSTANTS & CALIBRATION THRESHOLDS ---
#define TANK_HEIGHT_CM 100         // Total vertical depth of water storage tank
#define OVERFLOW_THRESHOLD_CM 15   // Distance <= 15 cm triggers critical overflow alarm
#define WARNING_THRESHOLD_CM 30    // Distance <= 30 cm triggers approaching high-level warning
#define SAMPLE_INTERVAL_MS 500     // Cadence between sensor readings in milliseconds
#define SAMPLES 5                  // Number of consecutive readings for moving-average filter

// --- GLOBAL OBJECTS & VARIABLES ---
LiquidCrystal_I2C lcd(0x27, 16, 2);

int distanceBuffer[SAMPLES];
int sampleIndex = 0;
bool bufferFilled = false;
unsigned long lastSampleTime = 0;

/**
 * @brief Measure raw distance in centimeters using HC-SR04 ultrasonic sensor.
 * @return Distance in centimeters, or -1 if timeout occurred.
 */
long measureRawDistanceCm() {
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(2);
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);
    digitalWrite(TRIG_PIN, LOW);

    // Measure echo pulse duration (timeout 30000 us ~ 5 meters max distance)
    long duration = pulseIn(ECHO_PIN, HIGH, 30000);
    if (duration == 0) {
        return -1; // Echo timeout
    }

    // Speed of sound = 343 m/s = 0.0343 cm/us -> Distance = (duration * 0.0343) / 2
    long distance = (duration * 0.0343) / 2;
    return distance;
}

/**
 * @brief Moving average filter to suppress acoustic reflections and noise.
 */
int getFilteredDistance(int newDistance) {
    if (newDistance < 0 || newDistance > TANK_HEIGHT_CM + 20) {
        return -1; // Reject out-of-bounds acoustic spikes
    }

    distanceBuffer[sampleIndex] = newDistance;
    sampleIndex = (sampleIndex + 1) % SAMPLES;
    if (sampleIndex == 0) {
        bufferFilled = true;
    }

    int count = bufferFilled ? SAMPLES : sampleIndex;
    if (count == 0) return newDistance;

    long sum = 0;
    for (int i = 0; i < count; i++) {
        sum += distanceBuffer[i];
    }
    return sum / count;
}

void setup() {
    // Configure GPIO Pin Modes
    pinMode(TRIG_PIN, OUTPUT);
    pinMode(ECHO_PIN, INPUT);
    pinMode(BUZZER_PIN, OUTPUT);
    pinMode(LED_RED_PIN, OUTPUT);
    pinMode(LED_GREEN_PIN, OUTPUT);
    pinMode(STATUS_LED_PIN, OUTPUT);

    // Initial output states (safe startup)
    digitalWrite(TRIG_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
    digitalWrite(LED_RED_PIN, LOW);
    digitalWrite(LED_GREEN_PIN, HIGH);
    digitalWrite(STATUS_LED_PIN, HIGH); // Inactive high

    // Initialize USART1 Telemetry
    Serial.begin(115200);
    delay(100);
    Serial.println(F("=================================================="));
    Serial.println(F("  WATER LEVEL DETECTION & OVERFLOW INDICATOR     "));
    Serial.println(F("  MCU: STM32F103C8T6 | Sensor: HC-SR04 | 16x2 LCD"));
    Serial.println(F("=================================================="));

    // Initialize I2C and 16x2 LCD
    Wire.begin();
    lcd.init();
    lcd.backlight();
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("WATER LEVEL SYS");
    lcd.setCursor(0, 1);
    lcd.print("INITIALIZING...");
    delay(1000);
    lcd.clear();
}

void loop() {
    unsigned long currentMillis = millis();

    if (currentMillis - lastSampleTime >= SAMPLE_INTERVAL_MS) {
        lastSampleTime = currentMillis;

        long rawDistance = measureRawDistanceCm();
        if (rawDistance < 0) {
            Serial.println(F("[ERROR] Sensor Echo Timeout. Checking alignment..."));
            lcd.setCursor(0, 0);
            lcd.print("SENSOR TIMEOUT! ");
            lcd.setCursor(0, 1);
            lcd.print("CHECK ALIGNMENT ");
            return;
        }

        int distanceCm = getFilteredDistance((int)rawDistance);
        int waterLevelPercent = map(distanceCm, TANK_HEIGHT_CM, OVERFLOW_THRESHOLD_CM, 0, 100);
        waterLevelPercent = constrain(waterLevelPercent, 0, 100);

        // Telemetry logging
        Serial.print(F("Dist: "));
        Serial.print(distanceCm);
        Serial.print(F(" cm | Level: "));
        Serial.print(waterLevelPercent);
        Serial.print(F("% | State: "));

        // State Machine Decision Logic
        if (distanceCm <= OVERFLOW_THRESHOLD_CM) {
            // STATE 1: CRITICAL OVERFLOW WARNING
            Serial.println(F("CRITICAL OVERFLOW ALERT"));
            
            digitalWrite(LED_RED_PIN, HIGH);
            digitalWrite(LED_GREEN_PIN, LOW);
            digitalWrite(BUZZER_PIN, HIGH);
            digitalWrite(STATUS_LED_PIN, LOW); // Active-low status LED ON

            lcd.setCursor(0, 0);
            lcd.print("DIST: ");
            lcd.print(distanceCm);
            lcd.print("cm [CRIT] ");

            lcd.setCursor(0, 1);
            lcd.print("OVERFLOW ALERT! ");
        }
        else if (distanceCm <= WARNING_THRESHOLD_CM) {
            // STATE 2: APPROACHING HIGH LEVEL WARNING
            Serial.println(F("APPROACHING HIGH LEVEL"));

            digitalWrite(LED_RED_PIN, HIGH);
            digitalWrite(LED_GREEN_PIN, HIGH);
            digitalWrite(BUZZER_PIN, LOW);
            digitalWrite(STATUS_LED_PIN, HIGH);

            lcd.setCursor(0, 0);
            lcd.print("DIST: ");
            lcd.print(distanceCm);
            lcd.print("cm [WARN] ");

            lcd.setCursor(0, 1);
            lcd.print("LEVEL HIGH      ");
        }
        else {
            // STATE 3: NORMAL / SAFE LEVEL
            Serial.println(F("NORMAL / SAFE"));

            digitalWrite(LED_RED_PIN, LOW);
            digitalWrite(LED_GREEN_PIN, HIGH);
            digitalWrite(BUZZER_PIN, LOW);
            digitalWrite(STATUS_LED_PIN, HIGH);

            lcd.setCursor(0, 0);
            lcd.print("DIST: ");
            lcd.print(distanceCm);
            lcd.print("cm [NORM] ");

            lcd.setCursor(0, 1);
            lcd.print("STATUS: NORMAL  ");
        }
    }
}
