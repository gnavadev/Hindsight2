"""
Privacy Window Module

Creates a window that is visible to the user but excluded from screenshots
and screen sharing:

- Windows: SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)
- macOS:   NSWindow.sharingType = NSWindowSharingNone
- KDE Plasma (Wayland): KWin script sets excludeFromCapture + keepAbove
  and positions the overlay (Wayland clients cannot do this themselves)
"""

import logging
import sys
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPoint

logger = logging.getLogger(__name__)

_kwin_rules = None
_kwin_checked = False


def kwin_rules():
    """Process-wide KWin window rules, or None when not on KDE Wayland."""
    global _kwin_rules, _kwin_checked
    if not _kwin_checked:
        _kwin_checked = True
        if sys.platform.startswith("linux"):
            from utils.kwin_window import KWinWindowRules, kwin_wayland_available
            if kwin_wayland_available():
                try:
                    _kwin_rules = KWinWindowRules()
                except Exception as e:
                    logger.error(f"KWin window control unavailable: {e}")
            else:
                logger.warning(
                    "Always-on-top and capture exclusion on Linux need KDE Plasma (Wayland)"
                )
    return _kwin_rules

# Windows API Constants
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Win10 2004+
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000


_osd_layer = None
_osd_checked = False


def kde_osd_layer():
    """KWin on-screen-display layer helper (above fullscreen apps), or None."""
    global _osd_layer, _osd_checked
    if not _osd_checked:
        _osd_checked = True
        if kwin_rules() is not None:
            try:
                from utils.kde_osd import KdeOsdLayer
                _osd_layer = KdeOsdLayer()
            except Exception as e:
                logger.warning(f"Overlay cannot go above fullscreen apps: {e}")
    return _osd_layer


def primary_screen():
    """The user's primary screen. On KDE Wayland ask KDE, since Qt's
    primaryScreen() there ignores the desktop's primary-display setting."""
    from PySide6.QtGui import QGuiApplication

    if kwin_rules() is not None:
        from utils.kwin_window import kde_primary_output
        name = kde_primary_output()
        for screen in QGuiApplication.screens():
            if screen.name() == name:
                return screen
    return QGuiApplication.primaryScreen()


def exclude_widget_from_capture(widget: QWidget, enabled: bool) -> bool:
    """Apply capture-exclusion to any top-level widget (main window, dialogs)."""
    if sys.platform == "win32":
        return _win_set_display_affinity(widget, enabled)
    if sys.platform == "darwin":
        from utils.macos_window import set_sharing_type
        return set_sharing_type(widget, enabled)
    rules = kwin_rules()
    if rules is not None:
        # One process-wide flag covers every window we own (overlay, notes
        # panel, dialogs); only the overlay's privacy toggle changes it.
        return rules.set_exclude_from_capture(enabled)
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

    def setVisible(self, visible: bool):
        if visible and not self.isVisible():
            osd = kde_osd_layer()
            if osd is not None:
                # KWin only re-layers a window when it is mapped, so the OSD
                # role must be on the wl_surface before the first show.
                self.winId()
                try:
                    osd.apply(self.windowHandle())
                except Exception as e:
                    logger.error(f"Could not raise overlay above fullscreen apps: {e}", exc_info=True)
        super().setVisible(visible)

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

    # Positioning: Wayland ignores move(); KWin places the overlay instead.

    def capture_excluded_by_compositor(self) -> bool:
        """True when the compositor itself keeps this window out of captures."""
        return kwin_rules() is not None and self._privacy_enabled

    def overlay_pos(self) -> QPoint:
        rules = kwin_rules()
        if rules is not None:
            pos = rules.window_position(self.windowTitle())
            if pos is not None:
                return QPoint(*pos)
        return self.pos()

    def move_overlay(self, x: int, y: int):
        rules = kwin_rules()
        if rules is not None:
            rules.set_window_position(self.windowTitle(), x, y)
        else:
            self.move(x, y)

    def overlay_center(self) -> QPoint:
        p = self.overlay_pos()
        return QPoint(p.x() + self.width() // 2, p.y() + self.height() // 2)

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
            # The attribute above only stops Qt from handling clicks; the
            # compositor still delivers them to us. This flag gives the
            # surface an empty input region (Wayland) / input shape (X11),
            # so clicks reach the window underneath.
            flag = Qt.WindowType.WindowTransparentForInput
            handle = self.windowHandle()
            if handle is not None:
                # Change the live window; QWidget.setWindowFlag would recreate
                # it (new wl_surface) and drop the on-screen-display role.
                handle.setFlag(flag, enabled)
            else:
                self.setWindowFlag(flag, enabled)
            logger.debug(f"Click-through {'enabled' if enabled else 'disabled'} (Linux)")

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
