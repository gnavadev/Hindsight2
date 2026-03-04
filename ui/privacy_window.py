"""
Privacy Window Module

Implements Windows API display affinity to create a window that:
- Is visible to the user
- Is invisible to screen sharing/recording software (appears transparent in captures)

Requires Windows 10 version 2004 (build 19041) or later.
"""

import ctypes
import logging
from ctypes import wintypes
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)

# Windows API Constants
WDA_NONE             = 0x00000000
WDA_MONITOR          = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Win10 2004+ — excluded region shows as transparent in captures
GWL_EXSTYLE          = -20
WS_EX_TRANSPARENT    = 0x00000020
WS_EX_LAYERED        = 0x00080000


class PrivacyWindow(QWidget):
    """
    Base privacy window that applies SetWindowDisplayAffinity to exclude
    the window from screen capture. Requires the window to be fully created
    (shown) before the affinity can be applied — do NOT call before show().
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # NOTE: _apply_display_affinity is intentionally NOT called here.
        # The HWND is not valid until the native window is created (first showEvent).
        self._privacy_pending = True  # Will be applied on first show

    def _get_hwnd(self) -> int:
        hwnd = int(self.winId())
        if not hwnd:
            raise RuntimeError("Window has no valid HWND yet")
        return hwnd

    def set_privacy_mode(self, enabled: bool):
        """
        Toggle exclusion from screen capture.
        When enabled the window region appears TRANSPARENT (not black) in
        captures on Windows 10 2004+ / Windows 11.
        The black-box symptom means the call is happening before the native
        window exists — this class ensures it only runs after showEvent.
        """
        try:
            hwnd     = self._get_hwnd()
            user32   = ctypes.windll.user32
            affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE

            result = user32.SetWindowDisplayAffinity(hwnd, affinity)
            if result == 0:
                err = ctypes.get_last_error()
                logger.warning(f"SetWindowDisplayAffinity failed (error {err})")
            else:
                logger.info(f"Privacy mode {'enabled' if enabled else 'disabled'}")

        except RuntimeError as e:
            # HWND not ready — will retry on next showEvent
            logger.debug(f"Privacy mode deferred: {e}")
            self._privacy_pending = enabled
        except Exception as e:
            logger.error(f"Error toggling privacy mode: {e}", exc_info=True)

    def _apply_display_affinity(self):
        self.set_privacy_mode(True)

    def showEvent(self, event):
        """Re-apply privacy every time the window becomes visible."""
        super().showEvent(event)
        self._apply_display_affinity()

    def set_click_through(self, enabled: bool):
        """Toggle click-through (mouse events pass to the window behind)."""
        try:
            hwnd   = self._get_hwnd()
            user32 = ctypes.windll.user32
            style  = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

            if enabled:
                new_style = style | WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                new_style = style & ~WS_EX_TRANSPARENT  # keep WS_EX_LAYERED for opacity

            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            logger.debug(f"Click-through {'enabled' if enabled else 'disabled'}")

        except Exception as e:
            logger.error(f"Error setting click-through: {e}", exc_info=True)