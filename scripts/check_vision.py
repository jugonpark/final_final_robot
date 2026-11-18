"""Offline ML smoke, optional brief webcam probe; never imports the UDP transport."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from python_controller.config import load_config
from python_controller.vision.person_detector import PersonDetector
from python_controller.vision.gesture_recognizer import GestureRecognizer


def main():
    import numpy as np
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera',action='store_true',help='Read three real webcam frames; no image is saved')
    args = parser.parse_args()
    cfg = load_config()
    root = Path(__file__).resolve().parents[1]
    for k in ('model_path','gesture_model_path'): cfg['vision'][k] = str(root/cfg['vision'][k])
    detector = PersonDetector(cfg['vision'])
    gesture = GestureRecognizer(cfg['vision'],cfg['gesture'])
    camera = None
    evidence = dict(input='WEBCAM' if args.camera else 'BLANK_SYNTHETIC_IMAGE',motor_packets_sent=0,frames=[])
    try:
        if args.camera:
            from python_controller.vision.camera import Camera
            camera = Camera(cfg['camera'])
        for _ in range(3):
            if camera: image,timestamp = camera.read()
            else: image,timestamp = np.zeros((480,640,3),dtype=np.uint8),time.monotonic()
            people = detector.detect(image,timestamp)
            result,hands = gesture.recognize(image,people,None,timestamp)
            evidence['frames'].append(dict(people=len(people),hands=len(hands),elapsed_ms=round((time.monotonic()-timestamp)*1000,2)))
        print(json.dumps(evidence,indent=2))
    finally:
        if camera: camera.close()
        gesture.close()


if __name__ == '__main__': main()
