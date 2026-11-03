import sys
import tempfile
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import bootstrap
temp = root.parent / '.analysis-temp'
temp.mkdir(exist_ok=True)
tempfile.tempdir = str(temp)
suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_*.py')
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
