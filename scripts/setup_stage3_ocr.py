"""Download verified Chinese OCR data to project-local .stage3/tessdata."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import urllib.request

root = Path(__file__).resolve().parents[1] / '.stage3/tessdata'
root.mkdir(parents=True, exist_ok=True)
url = 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_sim.traineddata'
expected = 'a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730'
data = urllib.request.urlopen(url, timeout=60).read()
if hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit('OCR artifact digest changed; review a new version explicitly')
(root / 'chi_sim.traineddata').write_bytes(data)
installed = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, check=True).stdout
shutil.copyfile(Path(installed.split('"')[1]) / 'eng.traineddata', root / 'eng.traineddata')
print(root)
