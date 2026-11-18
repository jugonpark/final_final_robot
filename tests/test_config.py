import json
import tempfile
from pathlib import Path
import unittest
from python_controller.config import load_config


class ConfigTests(unittest.TestCase):
    def test_unsafe_overrides_rejected(self):
        temp_root=Path(__file__).resolve().parents[1]/'.tmp'
        temp_root.mkdir(exist_ok=True)
        for patch in [{'follow':{'max_linear_cm_s':100}}, {'follow':{'distance_gain':-1}},
                      {'communication':{'signs_verified':'yes'}}, {'safety':{'perception_timeout_s':2}},
                      {'camera':{'index':1.5}}, {'follow':{'forward_sign':0}},
                      {'gesture':{'hold_s':0}}, {'vision':{'typo':1}}]:
            with self.subTest(patch=patch),tempfile.TemporaryDirectory(dir=temp_root) as tmp:
                path=Path(tmp)/'config.json';path.write_text(json.dumps(patch),encoding='utf-8')
                with self.assertRaises(ValueError): load_config(path)

    def test_custom_camera_adapter_setting_and_default_hardware_gate(self):
        cfg=load_config()
        self.assertFalse(cfg['communication']['signs_verified'])
        self.assertEqual(cfg['camera']['index'],0)
