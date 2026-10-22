"""Point d'entrée Passenger (cPanel « Setup Python App » chez o2switch)."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from web.app import app as application  # noqa: E402
