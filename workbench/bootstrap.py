"""Use the existing portable runtime and vendor packages without copying them."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORTABLE = Path(os.environ.get('MOCKUP_RUNTIME_ROOT', str(ROOT.parent / '通用PSD高速套图_正片叠底_2套并行便携版')))
sys.dont_write_bytecode = True
for location in (ROOT, PORTABLE / 'vendor', ROOT / 'core'):
    sys.path.insert(0, str(location))
_dll_handles = []
if os.name == 'nt':
    runtime = PORTABLE / 'runtime'
    for location in (runtime, runtime / 'DLLs', runtime / 'Lib/site-packages/numpy.libs', runtime / 'Lib/site-packages/PIL'):
        if location.is_dir():
            _dll_handles.append(os.add_dll_directory(str(location)))
    os.environ['TCL_LIBRARY'] = str(runtime / 'tcl/tcl8.6')
    os.environ['TK_LIBRARY'] = str(runtime / 'tcl/tk8.6')
