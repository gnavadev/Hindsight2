"""
Screenshot Capture Module

Fast screen capture using mss library
"""

import logging
import os
import secrets
import urllib.parse
from PIL import Image
import mss

logger = logging.getLogger(__name__)

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"


def _portal_screenshot():
    """Full-desktop screenshot via xdg-desktop-portal (no UI after the
    one-time permission prompt). Returns a PIL image or None."""
    from jeepney import DBusAddress, MatchRule, new_method_call
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import unwrap_msg
    from utils.hotkeys_linux import APP_ID, ensure_desktop_entry

    with open_dbus_connection(bus="SESSION") as conn:
        # Identify ourselves so the screenshot permission is remembered
        ensure_desktop_entry()
        try:
            reg = DBusAddress(_PORTAL_PATH, _PORTAL_BUS, "org.freedesktop.host.portal.Registry")
            unwrap_msg(conn.send_and_get_reply(
                new_method_call(reg, "Register", "sa{sv}", (APP_ID, {})), timeout=5
            ))
        except Exception as e:
            logger.debug(f"Portal app-id registration unavailable: {e}")

        token = "hs" + secrets.token_hex(8)
        sender = conn.unique_name.lstrip(":").replace(".", "_")
        rule = MatchRule(
            type="signal", interface="org.freedesktop.portal.Request", member="Response",
            path=f"{_PORTAL_PATH}/request/{sender}/{token}",
        )
        unwrap_msg(conn.send_and_get_reply(message_bus.AddMatch(rule)))
        with conn.filter(rule) as responses:
            shot = DBusAddress(_PORTAL_PATH, _PORTAL_BUS, "org.freedesktop.portal.Screenshot")
            unwrap_msg(conn.send_and_get_reply(new_method_call(
                shot, "Screenshot", "sa{sv}",
                ("", {"handle_token": ("s", token), "interactive": ("b", False)}),
            ), timeout=10))
            # Long timeout: the first call may wait on a permission dialog
            code, results = conn.recv_until_filtered(responses, timeout=120).body

    if code != 0:
        logger.warning(f"Screenshot portal {'denied' if code == 1 else 'failed'} (code {code})")
        return None
    path = urllib.parse.unquote(urllib.parse.urlparse(results["uri"][1]).path)
    try:
        img = Image.open(path).convert("RGB")  # fully loads before we delete
    finally:
        try:
            os.unlink(path)  # the portal saves into ~/Pictures; don't leave copies
        except OSError:
            pass
    return img


def _capture_wayland(center_point=None):
    try:
        img = _portal_screenshot()
        if img is None:
            return None

        # The portal returns the whole desktop, scaled uniformly from Qt's
        # logical coordinates; crop out the screen holding the overlay.
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication

        screen = None
        if center_point:
            screen = QGuiApplication.screenAt(QPoint(*center_point))
        if screen is None:
            from ui.privacy_window import primary_screen
            screen = primary_screen()
        if screen is None:
            return img
        virt = screen.virtualGeometry()
        geo = screen.geometry()
        scale = img.width / max(1, virt.width())
        box = (
            round((geo.x() - virt.x()) * scale),
            round((geo.y() - virt.y()) * scale),
            round((geo.x() - virt.x() + geo.width()) * scale),
            round((geo.y() - virt.y() + geo.height()) * scale),
        )
        img = img.crop(box)
        logger.info(f"Screenshot captured via portal (screen {screen.name()}): {img.size}")
        return img
    except Exception as e:
        logger.error(f"Error capturing screenshot via portal: {e}", exc_info=True)
        return None


def capture_screenshot(center_point=None):
    """
    Capture a screenshot of the monitor containing the center_point.
    If center_point is None, captures the primary monitor.

    Args:
        center_point: Tuple (x, y) coordinates
    """
    if os.environ.get("WAYLAND_DISPLAY"):
        # Wayland forbids reading the screen directly (mss returns black)
        return _capture_wayland(center_point)
    try:
        with mss.mss() as sct:
            monitor_idx = 1 # Default to primary

            if center_point:
                x, y = center_point
                logger.debug(f"Looking for monitor containing point ({x}, {y})")
                # Find which monitor contains the point
                # sct.monitors[0] is 'all monitors combined', so skip it
                for i, m in enumerate(sct.monitors[1:], 1):
                    if (m['left'] <= x < m['left'] + m['width']) and \
                       (m['top'] <= y < m['top'] + m['height']):
                        monitor_idx = i
                        break

            monitor = sct.monitors[monitor_idx]
            screenshot = sct.grab(monitor)

            # Convert to PIL Image
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')

            logger.info(f"Screenshot captured (Monitor {monitor_idx}): {img.size}")
            return img
    except Exception as e:
        logger.error(f"Error capturing screenshot: {e}", exc_info=True)
        return None


def capture_region(x, y, width, height):
    """
    Capture a specific region of the screen.

    Args:
        x: Left coordinate
        y: Top coordinate
        width: Width of region
        height: Height of region

    Returns:
        PIL Image object
    """
    try:
        with mss.mss() as sct:
            monitor = {"top": y, "left": x, "width": width, "height": height}
            screenshot = sct.grab(monitor)

            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')

            logger.info(f"Region captured: {img.size}")
            return img
    except Exception as e:
        logger.error(f"Error capturing region: {e}", exc_info=True)
        return None
