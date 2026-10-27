// ==============================================================================
// PROJECT: PLANT WATERING REMINDER USING STM32 AND SOIL MOISTURE SENSOR
// Microcontroller: STM32F103C8T6 (ARM Cortex-M3, 72MHz)
// Course: Microcontrollers Laboratory (PCCST504)
// ==============================================================================

#include <Arduino.h>

// Pin Definitions
#define SENSOR_PIN PA0       // Analog Soil Moisture Sensor Input (ADC1_IN0)
#define BUZZER_PIN PA1       // Active Buzzer Alert Driver (GPIO Output)
#define LED_PIN PC13         // Onboard Status Indicator LED (Active-Low)

// Calibration Constants for 12-bit ADC (0 - 4095)
#define AIR_VALUE 3700       // Sensor reading in completely dry air / soil
#define WATER_VALUE 1300     // Sensor reading submerged in saturated soil / water

// Operational Thresholds (in percentage %)
#define DRY_THRESHOLD_PERCENT 30  // Moisture < 30% indicates plant needs watering
#define WET_THRESHOLD_PERCENT 80  // Moisture > 80% indicates soil is saturated

// Communication & Timing
#define SERIAL_BAUD 115200
const int SAMPLE_INTERVAL_MS = 250;
const int SAMPLES = 8;

/**
 * Read average raw analog value from soil moisture sensor across multiple samples.
 */
int readSoilMoistureAverage() {
    long total = 0;
    for (int i = 0; i < SAMPLES; i++) {
        total += analogRead(SENSOR_PIN);
        delay(10);
    }
    return (int)(total / SAMPLES);
}

/**
 * Map raw ADC reading to moisture percentage (AIR = 0%, WATER = 100%).
 */
int calculateMoisturePercent(int rawAdc) {
    int constrained = constrain(rawAdc, WATER_VALUE, AIR_VALUE);
    return map(constrained, AIR_VALUE, WATER_VALUE, 0, 100);
}

/**
 * Update serial telemetry and display indicators.
 */
void updateDisplay(rawAdc, percent, state) {
    Serial.print("Raw ADC: ");
    Serial.print(rawAdc);
    Serial.print(" | Moisture: ");
    Serial.print(percent);
    Serial.print("% | State: ");
    Serial.println(state);
}

void setup() {
    Serial.begin(SERIAL_BAUD);
    
    // Configure GPIO pins
    pinMode(SENSOR_PIN, INPUT_ANALOG);
    pinMode(BUZZER_PIN, OUTPUT);
    pinMode(LED_PIN, OUTPUT);

    // Initial state: Buzzer off, onboard LED off (active-low HIGH)
    digitalWrite(BUZZER_PIN, LOW);
    digitalWrite(LED_PIN, HIGH);

    Serial.println("==================================================");
    Serial.println(" Plant Watering Reminder System Initialized");
    Serial.println(" Target MCU: STM32F103C8T6 | ADC: 12-Bit");
    Serial.println("==================================================");
}

void loop() {
    int rawValue = readSoilMoistureAverage();
    int moisturePercent = calculateMoisturePercent(rawValue);

    if (moisturePercent < DRY_THRESHOLD_PERCENT) {
        digitalWrite(BUZZER_PIN, HIGH);
        digitalWrite(LED_PIN, LOW); // Active-low LED ON
        updateDisplay(rawAdc, percent, state);
    } else if (moisturePercent > WET_THRESHOLD_PERCENT) {
        digitalWrite(BUZZER_PIN, LOW);
        digitalWrite(LED_PIN, HIGH); // Active-low LED OFF
        updateDisplay(rawAdc, percent, state);
    } else {
        digitalWrite(BUZZER_PIN, LOW);
        digitalWrite(LED_PIN, HIGH); // Active-low LED OFF
        updateDisplay(rawAdc, percent, state);
    }

    delay(SAMPLE_INTERVAL_MS);
}
