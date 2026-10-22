"""Verify this interpreter can use the MedMH prebuilt Windows Python packages."""
import struct
import sys

supported = sys.version_info[:2] in {(3, 11), (3, 12), (3, 13), (3, 14)}
normal = bool(getattr(sys, "_is_gil_enabled", lambda: True)())
valid = (
    sys.implementation.name == "cpython"
    and supported
    and struct.calcsize("P") == 8
    and normal
)
print(f"Python {sys.version.split()[0]}, {struct.calcsize('P') * 8}-bit; standard build: {normal}")
if not valid:
    print("MedMH requires standard 64-bit Python 3.11–3.14 (3.13 recommended).")
    sys.exit(1)
