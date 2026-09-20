"""
Privacy Window Module

Creates a window that is visible to the user but excluded from screenshots
and screen sharing:

- Windows: SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)
- macOS:   NSWindow.sharingType = NSWindowSharingNone
"""

import logging
import sys
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)

# Windows API Constants
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Win10 2004+
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000


def exclude_widget_from_capture(widget: QWidget, enabled: bool) -> bool:
    """Apply capture-exclusion to any top-level widget (main window, dialogs)."""
    if sys.platform == "win32":
        return _win_set_display_affinity(widget, enabled)
    if sys.platform == "darwin":
        from utils.macos_window import set_sharing_type
        return set_sharing_type(widget, enabled)
    logger.debug("Capture exclusion is not available on this platform")
    return False


def _win_set_display_affinity(widget: QWidget, enabled: bool) -> bool:
    try:
        import ctypes
        hwnd = int(widget.winId())
        if not hwnd:
            raise RuntimeError("Window has no valid HWND yet")
        user32 = ctypes.windll.user32
        affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
        result = user32.SetWindowDisplayAffinity(hwnd, affinity)
        if result == 0:
            err = ctypes.get_last_error()
            logger.warning(f"SetWindowDisplayAffinity failed (error {err})")
            return False
        logger.info(f"Privacy mode {'enabled' if enabled else 'disabled'} (Windows)")
        return True
    except Exception as e:
        logger.error(f"Error toggling Windows privacy: {e}", exc_info=True)
        return False


class PrivacyWindow(QWidget):
    """
    Base privacy window. Capture-exclusion is applied after the native
    window exists (first showEvent) and re-applied whenever it is shown.
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if sys.platform == "darwin":
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self._privacy_enabled = True
        self._click_through = False
        self._macos_configured = False

    def set_privacy_mode(self, enabled: bool):
        """
        Toggle exclusion from screen capture / screen share.
        enabled=True  -> invisible in captures
        enabled=False -> visible in captures (debug)
        """
        self._privacy_enabled = enabled
        if not self.winId():
            logger.debug("Privacy mode deferred until native window exists")
            return
        exclude_widget_from_capture(self, enabled)

    def _apply_display_affinity(self):
        self.set_privacy_mode(True)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "darwin" and not self._macos_configured:
            try:
                from utils.macos_window import configure_overlay_window
                configure_overlay_window(self)
                self._macos_configured = True
            except Exception as e:
                logger.error(f"macOS overlay configure failed: {e}", exc_info=True)
        self.set_privacy_mode(self._privacy_enabled)
        if self._click_through:
            self.set_click_through(True)

    def set_click_through(self, enabled: bool):
        """Toggle click-through (mouse events pass to the window behind)."""
        self._click_through = enabled
        if sys.platform == "win32":
            self._win_click_through(enabled)
        elif sys.platform == "darwin":
            try:
                from utils.macos_window import set_ignores_mouse
                set_ignores_mouse(self, enabled)
                logger.debug(f"Click-through {'enabled' if enabled else 'disabled'} (macOS)")
            except Exception as e:
                logger.error(f"Error setting macOS click-through: {e}", exc_info=True)
        else:
            self.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled
            )

    def _win_click_through(self, enabled: bool):
        try:
            import ctypes
            hwnd = int(self.winId())
            if not hwnd:
                raise RuntimeError("Window has no valid HWND yet")
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enabled:
                new_style = style | WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                new_style = style & ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            logger.debug(f"Click-through {'enabled' if enabled else 'disabled'} (Windows)")
        except Exception as e:
            logger.error(f"Error setting click-through: {e}", exc_info=True)
