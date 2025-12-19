#!/bin/bash
# Starts the camera stream on port 8888
libcamera-vid -t 0 --inline --listen -o tcp://0.0.0.0:8888