# Disc Golf Cleaner

This repository contains the code and documentation for the Disc Golf Cleaner project, an automated robotic system designed to clean disc golf discs using AI-powered dirt detection.

View the project website at: [https://disc-golf-project.vercel.app/#/design](https://disc-golf-project.vercel.app/#/design)

## Software & Control Logic

The system implements a state machine that orchestrates the entire cleaning process. The system continuously monitors inputs from buttons, rotary encoder, and camera, then controls outputs to motors, servos, and the LCD display based on the current operational state.

**Inputs:** Button presses (START/STOP), Rotary encoder (chuck motor RPM), Camera feed  
**Outputs:** Chuck motor relay, Central motor relay, Brush servo position, LCD display messages

## System Architecture

The software architecture follows a centralized control model, where a single Python-based state machine orchestrates all robot operations. To maintain modularity, hardware interactions are abstracted into logical blocks:

### Input Layer (Green)
Handles raw signals from the button interface, rotary encoder, and camera feed. This layer debounces mechanical switches and buffers video frames to prevent blocking the main loop.

### Logic Core (Blue)
The Main State Controller processes these inputs to make real-time decisions, such as detecting a stall condition or triggering a cleaning cycle. It also manages the API communication with the Roboflow cloud for defect detection.

### Output Layer (Red)
Translates high-level commands into physical actions, managing GPIO states for the relay modules and generating PWM signals for the brush servo and LCD interface.

## Camera Stream Architecture

To ensure low-latency image capture without blocking the main control loop, the system utilizes a split-process architecture. A dedicated video streaming service runs in a separate terminal instance using libcamera-vid. This service interfaces directly with the camera hardware and broadcasts a raw MJPEG stream over a local TCP socket (port 8888).

The main Python control script connects to this stream only when an image is required. This decoupling prevents the camera's initialization delay from slowing down the robot's real-time cleaning operations and allows for easier debugging of the video feed independent of the robot's logic.

**Video Stream:** libcamera-vid → TCP socket (port 8888) → OpenCV VideoCapture → Base64 encoding → Roboflow API

## State Machine Operation

The cleaning cycle operates through four distinct states, each handling a specific phase of the process:

1. **IDLE → WAIT_FOR_LOAD**  
   When START is pressed, the chuck motor opens for 3 seconds to allow disc insertion. The system then waits for the user to place the disc and press START again.

2. **WAIT_FOR_LOAD → CLEANING**  
   The chuck motor activates to clamp the disc. The rotary encoder monitors motor RPM, detecting when the motor stalls (indicating the disc is gripped). If no stall is detected within 5 seconds, the system returns to IDLE with an error message.  
   *Stall Detection:* Monitors encoder ticks. If the count remains unchanged for 20 consecutive loops (0.2s), the disc is considered clamped.

3. **CLEANING Cycle**  
   The brush servo lowers to the scrubbing position, and the central motor rotates the disc for 30 seconds. The LCD updates every 5 seconds showing remaining time. After cleaning, the brush retracts and the AI vision system analyzes the disc.

4. **CLEANING → FINISHED**  
   If the AI detects no dirt, the system moves to FINISHED state and displays "Clean Complete!" If dirt is still detected, the system loops back to CLEANING for another wash cycle.

## Machine Learning Dirt Detection

We trained a custom AI model using Roboflow, a web platform that automates AI-powered image analysis. The model was trained on a dataset of 25 clean disc images and 25 images with visible dirt, enabling it to accurately detect dirt presence on disc surfaces.

### Model Performance Visualization

The trained model demonstrates robust dirt detection capabilities, accurately identifying dirt particles and debris while ignoring disc features such as color, logos, and printed graphics. This selective detection is crucial for reliable operation across different disc designs and colors.

**Camera Input**  
Raw image captured by Pi Camera  
![Dirty disc as seen by camera](./dirty_disc.png)

**ML Model Detection**  
Dirt regions identified by AI (blue bounding boxes)  
![ML model dirt detection with bounding boxes](./dirt_detection.png)

As shown in the comparison above, the model successfully identifies individual dirt particles and debris clusters (highlighted with blue bounding boxes) while completely ignoring the disc's pink color, black logo artwork, and printed text. This demonstrates the model's ability to distinguish between actual dirt contamination and the disc's inherent design features, ensuring accurate cleanliness verification regardless of disc appearance.

### Roboflow Workflow

Our Roboflow workflow processes images through an automated pipeline:

- **Input:** Receives an image from the Raspberry Pi camera
- **Detection:** Runs a segmentation model (SAM3) to identify and segment dirt regions
- **Visualization:** Draws bounding boxes on detected dirt areas
- **Notification:** Sends results via webhook for logging
- **Data Upload:** Saves image and results to dataset for review/retraining
- **Boolean Output:** Returns "true" if dirt detected, "false" if clean

Explore the workflow: [View Roboflow Workflow](https://app.roboflow.com/workflows/embed/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ3b3JrZmxvd0lkIjoiZ2k0azZzeDFscWxwc1J1ZVd1YkIiLCJ3b3Jrc3BhY2VJZCI6IlFrWWFXQmI5NzNQWTNLN2pkYnlhVlBFMVV6eTIiLCJ1c2VySWQiOiJRa1lhV0JiOTczUFYzSzdqZGJ5YVZQRTFVenkyIiwiaWF0IjoxNzY2MDQ1MzY0fQ.kjzo9iuPqUFK4MCBv9G_rBWBhPuV9UrvZTv3FDnYCrU)

### Integration in Software

The `check_dirt_with_ai()` function captures an image from the camera, encodes it as base64, and sends it to the Roboflow workflow API:

```python
# Capture image from camera
cap = cv2.VideoCapture("tcp://127.0.0.1:8888")
ret, frame = cap.read()

# Encode and send to Roboflow
img_base64 = base64.b64encode(img_encoded).decode("utf-8")
response = requests.post(WORKFLOW_URL, json={
    "api_key": API_KEY,
    "inputs": {"image": {"type": "base64", "value": img_base64}}
})

# Extract boolean result
is_dirty = result.get("outputs", [{}])[0].get("boolean", 0)
return is_dirty == 1
```

The function returns `True` if dirt is detected, triggering another cleaning cycle. Otherwise, the disc is considered clean and the process completes.

## Safety & Error Handling

The system includes multiple safety mechanisms:

- **Emergency Stop:** The STOP button immediately halts all motors and returns the brush to home position, regardless of current state
- **Stall Timeout:** If the chuck fails to grip the disc within 5 seconds, the system safely returns to IDLE with an error message
- **AI Failure Handling:** If the Roboflow API call fails or times out, the system defaults to assuming the disc is clean to prevent infinite cleaning loops
- **LCD Feedback:** Real-time status messages guide the user through each phase of the cleaning process

## Active Servo Stabilization (Brownout Protection)

During the high-load cleaning phase, the central motor draws significant current, creating momentary voltage drops (brownouts) and electrical noise that can cause the brush servo to lose holding torque and drift upward. To counteract this, the software implements an active holding loop.

Instead of sending a single position command, the control loop re-asserts the "brush down" PWM signal at a frequency of 1Hz (once per second) throughout the entire wash cycle. This constant software reinforcement forces the servo to maintain pressure on the disc despite electrical fluctuations and mechanical vibrations, ensuring a consistent scrub.

**Implementation:** During 30-second wash cycle, `brush_servo.value = BRUSH_DOWN_POS` is called every 1 second (within the loop), providing continuous position reinforcement.

## External Software Dependencies

The software relies on the following external libraries and services:

### Operating System
- Raspberry Pi OS - Linux-based OS for Raspberry Pi hardware

### Programming Language
- Python 3 - Primary programming language

### Computer Vision
- OpenCV (cv2) - Image capture and processing
- libcamera-vid - Video streaming service for camera feed

### Hardware Control
- gpiozero - GPIO pin control for motors and buttons
- RPi.GPIO - Low-level GPIO access
- RPLCD - LCD display control library

### Network & API
- requests - HTTP library for Roboflow API calls

### Cloud Services
- Roboflow - Cloud-based ML model inference service

*Note: Standard Python libraries (time, base64, os, threading) are also used but are included with Python by default.*
