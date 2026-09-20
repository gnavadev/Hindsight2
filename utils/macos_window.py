"""
macOS native window helpers (no PyObjC).

Used to:
- Exclude the overlay from screenshots / screen share (NSWindowSharingNone)
- Click-through
- Hide the Dock icon
- Keep a Tool window visible across spaces
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import (
    CFUNCTYPE,
    c_bool,
    c_byte,
    c_char_p,
    c_long,
    c_ulong,
    c_void_p,
)
from typing import Optional

logger = logging.getLogger(__name__)

NSWindowSharingNone = 0
NSWindowSharingReadOnly = 1
NSWindowSharingReadWrite = 2

# NSWindowCollectionBehavior
_CAN_JOIN_ALL_SPACES = 1 << 0
_TRANSIENT = 1 << 3
_STATIONARY = 1 << 4
_IGNORES_CYCLE = 1 << 6
_FULL_SCREEN_AUXILIARY = 1 << 8

# NSWindow.Level
NSStatusWindowLevel = 25

NSApplicationActivationPolicyAccessory = 1

_BOOL = c_byte


class _ObjC:
    def __init__(self):
        self.lib = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.dylib")
        self.lib.objc_getClass.restype = c_void_p
        self.lib.objc_getClass.argtypes = [c_char_p]
        self.lib.sel_registerName.restype = c_void_p
        self.lib.sel_registerName.argtypes = [c_char_p]

    def cls(self, name: str) -> int:
        return self.lib.objc_getClass(name.encode("utf-8"))

    def sel(self, name: str) -> int:
        return self.lib.sel_registerName(name.encode("utf-8"))

    def send(self, obj, selector: str, *args, restype=c_void_p, argtypes=None):
        if not obj:
            return None
        sel = self.sel(selector)
        atypes = [c_void_p, c_void_p]
        if argtypes is not None:
            atypes.extend(argtypes)
        else:
            atypes.extend([c_void_p] * len(args))
        func = CFUNCTYPE(restype, *atypes)(("objc_msgSend", self.lib))
        return func(c_void_p(obj), c_void_p(sel), *args)


_objc: Optional[_ObjC] = None


def _load_appkit() -> None:
    # objc_getClass("NSWindow") is NULL until AppKit is loaded into the process.
    ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/Foundation.framework/Foundation"
    )
    appkit = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/AppKit.framework/AppKit"
    )
    try:
        appkit.NSApplicationLoad.restype = c_byte
        appkit.NSApplicationLoad()
    except Exception:
        pass


def _o() -> _ObjC:
    global _objc
    if _objc is None:
        _load_appkit()
        _objc = _ObjC()
    return _objc


def nsview_from_winid(win_id: int) -> Optional[int]:
    if not win_id:
        return None
    return int(win_id)


def nswindow_from_view(view: int) -> Optional[int]:
    window = _o().send(view, "window")
    return int(window) if window else None


def nswindow_from_widget(widget) -> Optional[int]:
    try:
        view = nsview_from_winid(int(widget.winId()))
        if not view:
            return None
        return nswindow_from_view(view)
    except Exception as e:
        logger.debug(f"nswindow_from_widget failed: {e}")
        return None


def set_sharing_type(widget, enabled_privacy: bool) -> bool:
    """
    enabled_privacy=True  -> NSWindowSharingNone (hidden from capture / share)
    enabled_privacy=False -> NSWindowSharingReadOnly (visible — debug)
    """
    window = nswindow_from_widget(widget)
    if not window:
        return False
    sharing = NSWindowSharingNone if enabled_privacy else NSWindowSharingReadOnly
    try:
        _o().send(
            window,
            "setSharingType:",
            c_ulong(sharing),
            restype=None,
            argtypes=[c_ulong],
        )
        logger.info(
            "macOS sharingType=%s",
            "None (hidden from capture)" if enabled_privacy else "ReadOnly (debug/visible)",
        )
        return True
    except Exception as e:
        logger.error(f"setSharingType failed: {e}", exc_info=True)
        return False


def set_ignores_mouse(widget, ignore: bool) -> bool:
    window = nswindow_from_widget(widget)
    if not window:
        return False
    try:
        _o().send(
            window,
            "setIgnoresMouseEvents:",
            _BOOL(1 if ignore else 0),
            restype=None,
            argtypes=[_BOOL],
        )
        return True
    except Exception as e:
        logger.error(f"setIgnoresMouseEvents failed: {e}", exc_info=True)
        return False


def configure_overlay_window(widget) -> bool:
    """Stay on top, all spaces, hidden from Cmd-Tab / Mission Control cycle."""
    window = nswindow_from_widget(widget)
    if not window:
        return False
    o = _o()
    try:
        behavior = (
            _CAN_JOIN_ALL_SPACES
            | _TRANSIENT
            | _STATIONARY
            | _IGNORES_CYCLE
            | _FULL_SCREEN_AUXILIARY
        )
        o.send(
            window,
            "setCollectionBehavior:",
            c_ulong(behavior),
            restype=None,
            argtypes=[c_ulong],
        )
        o.send(
            window,
            "setLevel:",
            c_long(NSStatusWindowLevel),
            restype=None,
            argtypes=[c_long],
        )
        o.send(
            window,
            "setHidesOnDeactivate:",
            _BOOL(0),
            restype=None,
            argtypes=[_BOOL],
        )
        o.send(
            window,
            "setCanHide:",
            _BOOL(0),
            restype=None,
            argtypes=[_BOOL],
        )
        return True
    except Exception as e:
        logger.error(f"configure_overlay_window failed: {e}", exc_info=True)
        return False


def hide_dock_icon() -> bool:
    """Accessory policy: no Dock tile, no app-switcher presence."""
    try:
        o = _o()
        nsapp_cls = o.cls("NSApplication")
        app = o.send(nsapp_cls, "sharedApplication")
        if not app:
            return False
        ok = o.send(
            app,
            "setActivationPolicy:",
            c_long(NSApplicationActivationPolicyAccessory),
            restype=c_bool,
            argtypes=[c_long],
        )
        logger.info("macOS activation policy set to Accessory (no Dock icon)")
        return bool(ok)
    except Exception as e:
        logger.error(f"hide_dock_icon failed: {e}", exc_info=True)
        return False
