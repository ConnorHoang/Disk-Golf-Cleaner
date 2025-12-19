import cv2
import time
import requests
import base64
import os
import threading
from gpiozero import OutputDevice, Servo, Button, DigitalInputDevice
from RPLCD.gpio import CharLCD
import RPi.GPIO as GPIO

# section 1: tuning and setup

# cloud ai settings
API_KEY = "YOUR_API_KEY"
WORKFLOW_URL = "https://detect.roboflow.com/infer/workflows/disc-golf-7sak2/find-dirts"

# brush servo tuning
BRUSH_UP_POS = -1    # home position
BRUSH_DOWN_POS = 0   # scrubbing position

# clamp settings
# note: this is how long the chuck spins to find grip (not wash time)
STALL_TIMEOUT = 5.0    
STALL_SENSITIVITY = 20 

# pinout mapping
PIN_START_BTN   = 16   
PIN_STOP_BTN    = 3  
PIN_ENCODER     = 1   
PIN_BRUSH_SERVO = 13  
PIN_CENTRAL_MTR = 17  
PIN_CHUCK_MTR   = 27  

# lcd pinout
LCD_RS = 26
LCD_E  = 19
LCD_D4 = 6
LCD_D5 = 5
LCD_D6 = 22
LCD_D7 = 23

# section 2: hardware objects

# lcd display setup
class RobotDisplay:
    def __init__(self):
        try:
            self.lcd = CharLCD(numbering_mode=GPIO.BCM, 
                               cols=16, rows=2, 
                               pin_rs=LCD_RS, pin_e=LCD_E, 
                               pins_data=[LCD_D4, LCD_D5, LCD_D6, LCD_D7])
            self.lcd.clear()
        except Exception:
            self.lcd = None

    def show(self, line1, line2=""):
        if self.lcd:
            try:
                self.lcd.clear()
                self.lcd.write_string(line1)
                self.lcd.cursor_pos = (1, 0)
                self.lcd.write_string(line2)
            except:
                pass

lcd = RobotDisplay()

# motors
chuck_relay = OutputDevice(PIN_CHUCK_MTR, active_high=True, initial_value=False)
central_relay = OutputDevice(PIN_CENTRAL_MTR, active_high=True, initial_value=False)

# servo
brush_servo = Servo(PIN_BRUSH_SERVO)

# buttons
try:
    btn_start = Button(PIN_START_BTN, pull_up=None, active_state=False)
    btn_stop = Button(PIN_STOP_BTN, pull_up=None, active_state=False)
except:
    pass

# encoder
encoder_pin = DigitalInputDevice(PIN_ENCODER)
encoder_ticks = 0

def _tick():
    global encoder_ticks
    encoder_ticks += 1
    
encoder_pin.when_activated = _tick

# section 3: helper functions

def system_stop_all():
    # safety kill switch
    chuck_relay.off()
    central_relay.off()
    brush_servo.value = BRUSH_UP_POS
    time.sleep(0.5)
    brush_servo.detach() 

def check_dirt_with_ai():
    # takes photo and asks roboflow if dirty
    lcd.show("Scanning...", "Analyzing Disc")
    
    cap = cv2.VideoCapture("tcp://127.0.0.1:8888")
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    
    # flush buffer
    for _ in range(10): cap.grab()
    
    ret, frame = cap.read()
    cap.release()

    if not ret: 
        return False

    # encode image
    _, img_encoded = cv2.imencode('.jpg', frame)
    img_base64 = base64.b64encode(img_encoded.tobytes()).decode("utf-8")
    
    payload = {
        "api_key": API_KEY,
        "inputs": {"image": {"type": "base64", "value": img_base64}}
    }

    try:
        response = requests.post(WORKFLOW_URL, json=payload, timeout=8)
        if response.status_code == 200:
            result = response.json()
            is_dirty = result.get("outputs", [{}])[0].get("boolean", 0)
            return is_dirty == 1
    except:
        pass
    
    return False

# section 4: main loop

def main():
    state = "IDLE"
    
    # init
    brush_servo.value = BRUSH_UP_POS
    time.sleep(0.5)
    lcd.show("System Ready", "Press Start")

    while True:
        # e-stop check
        if btn_stop.is_pressed:
            system_stop_all()
            state = "IDLE"
            lcd.show("E-STOP PRESSED", "Resetting...")
            while btn_stop.is_pressed: time.sleep(0.1)
            time.sleep(2)
            continue 

        # phase 1: open chuck
        if state == "IDLE":
            lcd.show("Switch: OPEN", "Press GREEN")
            
            if btn_start.is_pressed:
                lcd.show("Opening Chuck", "Please Wait...")
                chuck_relay.on()
                time.sleep(3.0) 
                chuck_relay.off()
                state = "WAIT_FOR_LOAD"
                time.sleep(1)

        # phase 2: clamp chuck
        elif state == "WAIT_FOR_LOAD":
            lcd.show("Load Disc", "Switch: CLAMP")
            
            if btn_start.is_pressed:
                lcd.show("Clamping...", "Checking Grip")
                chuck_relay.on()
                
                # stall detection
                global encoder_ticks
                encoder_ticks = 0
                last_count = -1
                same_count_loops = 0
                start_t = time.time()
                is_clamped = False
                
                while (time.time() - start_t) < STALL_TIMEOUT:
                    if encoder_ticks == last_count:
                        same_count_loops += 1
                    else:
                        same_count_loops = 0 
                    
                    last_count = encoder_ticks
                    
                    if same_count_loops > STALL_SENSITIVITY:
                        is_clamped = True
                        break
                    
                    if btn_stop.is_pressed:
                        chuck_relay.off()
                        return 
                    
                    time.sleep(0.01)
                
                chuck_relay.off()
                
                if is_clamped:
                    state = "CLEANING"
                    lcd.show("Disc Secured", "Starting Wash")
                    time.sleep(1.5)
                else:
                    lcd.show("Error: No Grip", "Try Again")
                    time.sleep(2)
                    state = "IDLE"

        # phase 3: cleaning cycle
        elif state == "CLEANING":
            lcd.show("Cleaning...", "Brush Down")
            
            # 1. lower brush
            brush_servo.value = BRUSH_DOWN_POS
            time.sleep(1)
            
            # 2. start washer motor
            central_relay.on()
            
            # 3. wash timer (30 seconds)
            for i in range(30):
                if btn_stop.is_pressed: break
                
                # refresh screen every 5 seconds
                if i % 5 == 0: 
                    lcd.show("Washing...", f"{30-i}s Left")
                
                # active servo hold to fight vibration
                brush_servo.value = BRUSH_DOWN_POS
                time.sleep(1)
            
            # 4. stop motor and lift brush
            central_relay.off()
            brush_servo.value = BRUSH_UP_POS
            time.sleep(1)
            
            # 5. check ai result
            if check_dirt_with_ai():
                # if dirt is found, we loop back to 'cleaning' state
                lcd.show("Dirt Found!", "Washing Again")
                time.sleep(2)
            else:
                # if clean, we move to finish
                state = "FINISHED"

        # phase 4: finish
        elif state == "FINISHED":
            lcd.show("Clean Complete!", "Grab Disc")
            time.sleep(4)
            state = "IDLE" 

        time.sleep(0.05)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        system_stop_all()