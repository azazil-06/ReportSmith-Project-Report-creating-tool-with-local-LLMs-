/*
 * =====================================================================================
 * PROJECT 3: AUTOMATIC PROXIMITY DOOR OPENER USING ULTRASONIC SENSOR & SERVO
 * Target Microcontroller: STM32 (e.g., STM32F103C8T6 "Blue Pill" / STM32 Nucleo)
 * =====================================================================================
 *
 * HARDWARE CONNECTIONS & PIN ASSIGNMENTS:
 * -------------------------------------------------------------------------------------
 * 1. HC-SR04 ULTRASONIC SENSOR:
 *    - VCC   ---> 5V (HC-SR04 requires 5V supply)
 *    - GND   ---> STM32 GND
 *    - TRIG  ---> STM32 A0 (PA0)
 *    - ECHO  ---> STM32 A1 (PA1)
 *      *IMPORTANT NOTE ON 5V ECHO PIN*:
 *       STM32 GPIO pins operate at 3.3V logic. HC-SR04 ECHO sends a 5V pulse.
 *       It is highly recommended to use a simple 2-resistor voltage divider:
 *         HC-SR04 ECHO ---> 1kΩ ---> STM32 A1 (PA1) ---> 2kΩ ---> GND
 *       (This safely steps down 5V to ~3.3V).
 *
 * 2. SERVO MOTOR (SG90 / MG90S):
 *    - VCC (Red)    ---> 5V (External 5V supply recommended; connect all GNDs together)
 *    - GND (Brown)  ---> STM32 GND
 *    - PWM (Orange) ---> STM32 A11 (PA11 - Timer 1 Channel 4 PWM pin)
 *
 * 3. STATUS INDICATOR LEDS:
 *    - GREEN LED (Door OPEN / Proximity)  ---> STM32 PB0 (via 220Ω resistor to GND)
 *    - RED LED   (Door CLOSED / Clear)    ---> STM32 PB1 (via 220Ω resistor to GND)
 *    - Onboard LED                        ---> STM32 PC13 (Built-in Blue Pill LED, Active LOW)
 * -------------------------------------------------------------------------------------
 *
 * STM32 DEPENDENCIES REQUIRED:
 * - STM32 Core for Arduino (Official STMicroelectronics:stm32)
 * - Servo.h (Standard Arduino / STM32 Servo library)
 * =====================================================================================
 */

#include <Servo.h>

// ======================== PIN INITIALIZATION ========================
// On STM32 boards (Blue Pill, etc.):
// - A0 maps to PA0
// - A1 maps to PA1
// - A11 maps to PA11 (TIM1_CH4)
#ifndef PA11
  #define PA11 A11
#endif
#ifndef A11
  #define A11 PA11
#endif

#define TRIG_PIN           A0     // Ultrasonic Trigger Pin (A0 / PA0)
#define ECHO_PIN           A1     // Ultrasonic Echo Pin (A1 / PA1)
#define SERVO_PIN          PA11   // Servo PWM Control Signal Pin (A11 / PA11)
#define LED_DOOR_OPEN      PB0    // Green LED indicates proximity / open (180°)
#define LED_DOOR_CLOSED    PB1    // Red LED indicates no proximity / closed (0°)
#define STATUS_LED         PC13   // Built-in activity LED (Active LOW)

// ===================== CONFIGURATION & SETTINGS ======================
const int TRIGGER_DISTANCE_CM    = 30;    // Proximity threshold: detection within <= 30 cm
const int MAX_SENSOR_DISTANCE_CM = 400;   // Maximum HC-SR04 range (cm)
const int DOOR_CLOSED_ANGLE      = 0;     // Servo angle when NO proximity (0 degrees)
const int DOOR_OPEN_ANGLE        = 180;   // Servo angle when PROXIMITY detected (180 degrees)
const int SERVO_STEP_DELAY_MS    = 6;     // Motion speed: delay ms per degree (0 = instant snap, 5-10 = smooth)

// OPERATION MODE:
// - false: DIRECT MODE: Immediately snaps/moves to 180° on proximity, returns to 0° when clear.
// - true:  DOOR OPENER MODE: Holds open for HOLD_OPEN_MS after object leaves before closing.
const bool USE_HOLD_TIMER        = false; 
const unsigned long HOLD_OPEN_MS = 2000;  // Duration to hold at 180° after object leaves (if USE_HOLD_TIMER = true)

// Noise rejection filter for direct mode (prevents servo jitter on a single dropped echo)
const unsigned long CLEAR_DEBOUNCE_MS = 200; // Time no proximity must persist before returning to 0°

// ========================== ENUMS & GLOBALS ==========================
enum DoorState {
  STATE_CLOSED,
  STATE_OPENING,
  STATE_OPEN,
  STATE_CLOSING
};

DoorState currentDoorState = STATE_CLOSED;
Servo doorServo;

unsigned long doorOpenedTimestamp = 0;
unsigned long lastProximityDetectedTimestamp = 0;
int currentServoAngle = DOOR_CLOSED_ANGLE;

// ======================== HELPER PROTOTYPES ==========================
long measureDistanceCm();
void setDoorPosition(int targetAngle);

// ============================= SETUP =================================
void setup() {
  // Initialize Serial Monitor
  Serial.begin(115200);
  delay(100);
  Serial.println(F("\n--- STM32 Proximity Door Opener / Servo Controller Starting ---"));
  Serial.println(F("Pins: TRIG=A0 (PA0), ECHO=A1 (PA1), SERVO_PWM=A11 (PA11)"));
  Serial.print(F("Angles: No Proximity = "));
  Serial.print(DOOR_CLOSED_ANGLE);
  Serial.print(F("°, Proximity Detected = "));
  Serial.print(DOOR_OPEN_ANGLE);
  Serial.println(F("°"));

  // Configure Pins
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_DOOR_OPEN, OUTPUT);
  pinMode(LED_DOOR_CLOSED, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);

  // Ensure Trigger starts LOW
  digitalWrite(TRIG_PIN, LOW);

  // Attach and position Servo
  doorServo.attach(SERVO_PIN);
  doorServo.write(DOOR_CLOSED_ANGLE);
  currentServoAngle = DOOR_CLOSED_ANGLE;

  // Initialize LEDs (Door starts CLOSED / No Proximity)
  digitalWrite(LED_DOOR_OPEN, LOW);
  digitalWrite(LED_DOOR_CLOSED, HIGH);
  digitalWrite(STATUS_LED, HIGH); // Built-in LED OFF (Active LOW)

  Serial.println(F("[SUCCESS] Initialized at 0°. Scanning for proximity..."));
}

// =========================== MAIN LOOP ===============================
void loop() {
  // 1. Measure current proximity distance
  long distanceCm = measureDistanceCm();
  bool isProximity = (distanceCm > 0 && distanceCm <= TRIGGER_DISTANCE_CM);

  if (isProximity) {
    lastProximityDetectedTimestamp = millis();
  }

  // Telemetry print every 400ms
  static unsigned long lastLog = 0;
  if (millis() - lastLog >= 400) {
    lastLog = millis();
    Serial.print(F("Distance: "));
    if (distanceCm > 0 && distanceCm <= MAX_SENSOR_DISTANCE_CM) {
      Serial.print(distanceCm);
      Serial.print(F(" cm"));
    } else {
      Serial.print(F("Out of range"));
    }
    Serial.print(F(" | Proximity: "));
    Serial.print(isProximity ? F("DETECTED (180°)") : F("CLEAR (0°)"));
    Serial.print(F(" | Servo: "));
    Serial.print(currentServoAngle);
    Serial.println(F("°"));
  }

  // ===================== 2. SERVO CONTROL LOGIC ======================
  if (!USE_HOLD_TIMER) {
    // ---------------- DIRECT PROXIMITY MODE ----------------
    // If proximity detected -> move servo to 180°
    // If no proximity (confirmed after brief debounce) -> move servo to 0°
    if (isProximity) {
      if (currentServoAngle != DOOR_OPEN_ANGLE) {
        Serial.println(F(">> [PROXIMITY DETECTED] Setting servo to 180°"));
        digitalWrite(STATUS_LED, LOW);        // Activity LED ON
        digitalWrite(LED_DOOR_CLOSED, LOW);
        setDoorPosition(DOOR_OPEN_ANGLE);
        digitalWrite(LED_DOOR_OPEN, HIGH);
        digitalWrite(STATUS_LED, HIGH);
      }
    } else {
      // Return to 0° if no proximity is detected and debounce time has passed
      if (currentServoAngle != DOOR_CLOSED_ANGLE && 
          (millis() - lastProximityDetectedTimestamp >= CLEAR_DEBOUNCE_MS)) {
        Serial.println(F(">> [NO PROXIMITY] Setting servo to 0°"));
        digitalWrite(STATUS_LED, LOW);        // Activity LED ON
        digitalWrite(LED_DOOR_OPEN, LOW);
        setDoorPosition(DOOR_CLOSED_ANGLE);
        digitalWrite(LED_DOOR_CLOSED, HIGH);
        digitalWrite(STATUS_LED, HIGH);
      }
    }
  } else {
    // ---------------- SMART DOOR OPENER MODE ----------------
    switch (currentDoorState) {
      case STATE_CLOSED:
        if (isProximity) {
          Serial.println(F(">> [PROXIMITY DETECTED] Opening door to 180°..."));
          currentDoorState = STATE_OPENING;
        }
        break;

      case STATE_OPENING:
        digitalWrite(LED_DOOR_CLOSED, LOW);
        digitalWrite(STATUS_LED, LOW);
        
        setDoorPosition(DOOR_OPEN_ANGLE);
        
        digitalWrite(LED_DOOR_OPEN, HIGH);
        digitalWrite(STATUS_LED, HIGH);
        doorOpenedTimestamp = millis();
        currentDoorState = STATE_OPEN;
        Serial.println(F(">> Door at 180°. Holding open while obstacle present..."));
        break;

      case STATE_OPEN:
        // Reset timer as long as obstacle is still in front of sensor
        if (isProximity) {
          doorOpenedTimestamp = millis();
        }

        // Close once hold duration has elapsed with no obstacle
        if (millis() - doorOpenedTimestamp >= HOLD_OPEN_MS) {
          Serial.println(F(">> [NO PROXIMITY] Doorway clear. Closing to 0°..."));
          currentDoorState = STATE_CLOSING;
        }
        break;

      case STATE_CLOSING:
        digitalWrite(LED_DOOR_OPEN, LOW);
        digitalWrite(STATUS_LED, LOW);
        
        setDoorPosition(DOOR_CLOSED_ANGLE);
        
        digitalWrite(LED_DOOR_CLOSED, HIGH);
        digitalWrite(STATUS_LED, HIGH);
        currentDoorState = STATE_CLOSED;
        Serial.println(F(">> Door fully closed at 0°. Monitoring for proximity."));
        break;
    }
  }

  delay(40); // Fast sampling cycle
}

// ==================== ULTRASONIC MEASUREMENT =========================
long measureDistanceCm() {
  // Send 10 microsecond trigger pulse
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // Measure ECHO pulse length (Timeout: 25000 microseconds ~ 4.2 meters)
  long durationUs = pulseIn(ECHO_PIN, HIGH, 25000);

  if (durationUs == 0) {
    return -1; // No echo received / out of range
  }

  // Speed of sound = 343 m/s = 0.0343 cm/microsecond
  // Distance = (Duration * 0.0343) / 2
  long distance = durationUs * 0.0343 / 2;
  return distance;
}

// =================== SERVO MOTION HELPER =============================
void setDoorPosition(int targetAngle) {
  // If step delay is 0, snap directly to angle
  if (SERVO_STEP_DELAY_MS <= 0) {
    doorServo.write(targetAngle);
    currentServoAngle = targetAngle;
    return;
  }

  // Otherwise, sweep smoothly to prevent gear strain and abrupt mechanical shocks
  if (currentServoAngle < targetAngle) {
    for (int angle = currentServoAngle; angle <= targetAngle; angle++) {
      doorServo.write(angle);
      delay(SERVO_STEP_DELAY_MS);
    }
  } else if (currentServoAngle > targetAngle) {
    for (int angle = currentServoAngle; angle >= targetAngle; angle--) {
      doorServo.write(angle);
      delay(SERVO_STEP_DELAY_MS);
    }
  }
  currentServoAngle = targetAngle;
}

