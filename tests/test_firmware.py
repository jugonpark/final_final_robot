import hashlib
import json
from pathlib import Path
import re
import unittest
import zipfile


class FirmwareTests(unittest.TestCase):
    def test_reference_provenance_and_no_copied_credentials(self):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'esp32_firmware/source_manifest.json').read_text(encoding='utf-8'))
        archive=root/manifest['source_archive']
        if not archive.exists(): self.skipTest('Original private archive intentionally not distributed')
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),manifest['archive_sha256'])
        reference=root/'esp32_firmware/esp32_omni_controller'
        self.assertFalse((reference/'secrets.h').exists())
        with zipfile.ZipFile(archive) as z:
            source=z.read('esp32_omni_controller/esp32_omni_controller.ino').decode('utf-8-sig')
            tokens=re.findall(r'#define WIFI_(?:SSID|PASSWORD) "([^"]+)"',source)
            for name,hashes in manifest['files'].items():
                self.assertEqual(hashlib.sha256((reference/name).read_bytes()).hexdigest(),hashes['reference_sha256'])
                if name!='esp32_omni_controller.ino':
                    self.assertEqual(hashes['original_sha256'],hashes['reference_sha256'])
            self.assertIn('#error "Create a local secrets.h',(reference/'esp32_omni_controller.ino').read_text())
            for folder in ['python_controller','scripts','tests','esp32_firmware','docs']:
                for p in (root/folder).rglob('*'):
                    if p.is_file() and p.suffix in ('.py','.md','.json','.ino','.cpp','.h'):
                        content=p.read_text(encoding='utf-8',errors='ignore')
                        self.assertFalse(any('"'+v+'"' in content for v in tokens),'Credential copied into '+str(p.relative_to(root)))
