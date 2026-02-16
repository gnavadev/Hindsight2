"""Quick test script to check for import errors"""
import sys
import traceback

try:
    from ui.main_window import MainWindow
    print("SUCCESS: main_window imported")
except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
