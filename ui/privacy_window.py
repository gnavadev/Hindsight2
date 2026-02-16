"""
Privacy Window Module

Implements Windows API display affinity to create a window that:
- Is visible to the user
- Is invisible to screen sharing/recording software
"""

import ctypes
from ctypes import wintypes
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt


# Windows API Constants
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

class PrivacyWindow(QWidget):
    """
    Base privacy window class that applies Windows API display affinity
    to exclude the window from screen capture.
    """
    
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # Privacy setup
        self._apply_display_affinity()
        
    def _get_hwnd(self):
        """Get the correct HWND for the window"""
        # In PySide6, winId() returns the HWND on Windows
        return int(self.winId())

    def set_privacy_mode(self, enabled: bool):
        """
        Toggle privacy mode (exclusion from screen capture).
        Note: When enabled, this creates a black box in screen shares.
        """
        try:
            hwnd = self._get_hwnd()
            if not hwnd:
                print("No HWND found for privacy toggle")
                return

            user32 = ctypes.windll.user32
            affinity = WDA_EXCLUDEFROMCAPTURE if enabled else 0x00000000  # WDA_NONE
            
            result = user32.SetWindowDisplayAffinity(hwnd, affinity)
            
            if result == 0:
                error_code = ctypes.get_last_error()
                print(f"Warning: Failed to set display affinity. Error: {error_code}")
            else:
                state = "enabled" if enabled else "disabled"
                print(f"✓ Privacy mode {state}")
                
        except Exception as e:
            print(f"Error toggling privacy mode: {e}")

    def _apply_display_affinity(self):
        """Apply SetWindowDisplayAffinity to exclude from capture"""
        self.set_privacy_mode(True)

    def showEvent(self, event):
        """Re-apply privacy when window is shown"""
        super().showEvent(event)
        self._apply_display_affinity()
        
    def set_click_through(self, enabled: bool):
        """
        Toggle click-through mode.
        If enabled, the window ignores mouse clicks (passed to window behind).
        """
        try:
            hwnd = self._get_hwnd()
            user32 = ctypes.windll.user32
            
            # Get current styles
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            
            if enabled:
                # Add transparent and layered styles
                new_style = style | WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                # Remove transparent style (keep layered if needed)
                new_style = style & ~WS_EX_TRANSPARENT
                
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            
        except Exception as e:
            print(f"Error setting click-through: {e}")
