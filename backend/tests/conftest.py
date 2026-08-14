import os
import sys

# backend/ を import path に載せる（pseudonymizer パッケージを解決するため）
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
