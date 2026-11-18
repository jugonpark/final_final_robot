"""Explicit asset setup; the controller never downloads models while controlling motion."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    'yolo11n.pt':'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt',
    'gesture_recognizer.task':'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task'
}


def main():
    folder = ROOT/'models'
    folder.mkdir(exist_ok=True)
    manifest = {}
    for name,url in ASSETS.items():
        target = folder/name
        if not target.is_file():
            print(f'Downloading {name} from official publisher')
            temporary = target.with_suffix(target.suffix+'.part')
            try:
                with urllib.request.urlopen(url,timeout=30) as response, temporary.open('wb') as output:
                    while block := response.read(1024*1024): output.write(block)
                if temporary.stat().st_size < 100000: raise ValueError('Model download is incomplete')
                temporary.replace(target)
            finally:
                if temporary.exists(): temporary.unlink()
        manifest[name] = dict(url=url,bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        print(f'Ready: {name} ({target.stat().st_size} bytes)')
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__': main()
