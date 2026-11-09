"""
Global hotkeys.

Windows: RegisterHotKey + WM_HOTKEY via QWidget.nativeEvent
macOS:   Carbon RegisterEventHotKey on the *dispatcher* target
         (GetApplicationEventTarget never fires inside a Qt/Cocoa app)
         plus an NSEvent native filter as a second path.
Linux:   xdg-desktop-portal GlobalShortcuts (Wayland) or XGrabKey (X11);
         see utils/hotkeys_linux.py.

macOS chord is Control+Command (⌃⌘). Plain Control is eaten by Emacs
bindings, Terminal, and Mission Control (Control+Arrows).
⌃⌘H/B/P/R/Enter/Arrows are not default macOS shortcuts
(⌃⌘F is fullscreen, ⌃⌘Q locks the screen, ⌃⌘Space is the character viewer).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

Callback = Callable[[], None]


def platform_mod() -> str:
    return "ctrl+cmd" if sys.platform == "darwin" else "ctrl"


def chord_label(key: str) -> str:
    if sys.platform == "darwin":
        return f"⌃⌘{key}"
    return f"Ctrl+{key}"


class HotkeyManager:
    def register(self, hk_id: int, mod: str, key: str, callback: Callback,
                 repeat: bool = False) -> bool:
        raise NotImplementedError

    def unregister(self, hk_id: int) -> None:
        raise NotImplementedError

    def handle_native_event(self, event_type, message):
        return None


class _NoopHotkeys(HotkeyManager):
    """Fallback for platforms without a global-hotkey backend (e.g. Linux)."""

    def register(self, hk_id: int, mod: str, key: str, callback: Callback,
                 repeat: bool = False) -> bool:
        return False

    def unregister(self, hk_id: int) -> None:
        pass


def create_hotkey_manager(widget) -> HotkeyManager:
    if sys.platform == "win32":
        return _WinHotkeys(widget)
    if sys.platform == "darwin":
        return _MacHotkeys()
    if sys.platform.startswith("linux"):
        from utils.hotkeys_linux import create_linux_hotkey_manager

        mgr = create_linux_hotkey_manager()
        if mgr is not None:
            return mgr
    logger.warning("Global hotkeys are not implemented on this platform")
    return _NoopHotkeys()


# ── Windows ──────────────────────────────────────────────────────────


class _WinHotkeys(HotkeyManager):
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008
    WM_HOTKEY = 0x0312

    _VK = {
        "h": 0x48,
        "b": 0x42,
        "p": 0x50,
        "r": 0x52,
        "q": 0x51,
        "]": 0xDD,  # VK_OEM_6
        "o": 0x4F,
        "w": 0x57,
        "a": 0x41,
        "s": 0x53,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "enter": 0x0D,
        "return": 0x0D,
    }

    def __init__(self, widget):
        from ctypes import wintypes

        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._hwnd = int(widget.winId())
        self._callbacks: Dict[int, Callback] = {}

    def _mod(self, name: str) -> int:
        bits = 0
        for part in name.lower().replace(" ", "").split("+"):
            if part in ("ctrl", "control"):
                bits |= self.MOD_CONTROL
            elif part in ("cmd", "command", "win", "meta"):
                bits |= self.MOD_WIN
            elif part in ("alt", "option"):
                bits |= self.MOD_ALT
            elif part == "shift":
                bits |= self.MOD_SHIFT
        return bits

    def register(self, hk_id: int, mod: str, key: str, callback: Callback,
                 repeat: bool = False) -> bool:
        vk = self._VK.get(key.lower())
        if vk is None:
            logger.warning(f"Unknown hotkey key: {key}")
            return False
        if self._user32.RegisterHotKey(self._hwnd, hk_id, self._mod(mod), vk):
            self._callbacks[hk_id] = callback
            logger.info(f"Registered Windows hotkey ID {hk_id} ({mod}+{key})")
            return True
        logger.warning(f"Failed to register Windows hotkey ID {hk_id}")
        return False

    def unregister(self, hk_id: int) -> None:
        if hk_id not in self._callbacks:
            return
        if self._user32.UnregisterHotKey(self._hwnd, hk_id):
            self._callbacks.pop(hk_id, None)
            logger.debug(f"Unregistered Windows hotkey ID {hk_id}")

    def handle_native_event(self, event_type, message):
        try:
            if event_type == b"windows_generic_MSG":
                msg = self._wintypes.MSG.from_address(int(message))
                if msg.message == self.WM_HOTKEY:
                    hk_id = msg.wParam
                    cb = self._callbacks.get(hk_id)
                    if cb:
                        cb()
                        return True, 0
        except Exception as e:
            logger.error(f"Error in nativeEvent hotkey handler: {e}", exc_info=True)
        return None


# ── macOS Carbon ─────────────────────────────────────────────────────


def _fourcc(tag: bytes) -> int:
    return int.from_bytes(tag[:4].ljust(4, b"\0"), "big")


class _MacHotkeys(HotkeyManager):
    controlKey = 0x1000
    cmdKey = 0x0100
    shiftKey = 0x0200
    optionKey = 0x0800

    # NSEvent
    NSEventTypeKeyDown = 10
    NSEventTypeSystemDefined = 14
    NSEventModifierFlagControl = 1 << 18
    NSEventModifierFlagCommand = 1 << 20
    NSEventModifierFlagShift = 1 << 17
    NSEventModifierFlagOption = 1 << 19
    NSDeviceIndependentFlagsMask = 0xFFFF0000

    _KC = {
        "h": 0x04,
        "b": 0x0B,
        "p": 0x23,
        "r": 0x0F,
        "q": 0x0C,
        "]": 0x1E,  # kVK_ANSI_RightBracket
        "o": 0x1F,
        "w": 0x0D,
        "a": 0x00,
        "s": 0x01,
        "left": 0x7B,
        "right": 0x7C,
        "down": 0x7D,
        "up": 0x7E,
        "enter": 0x24,
        "return": 0x24,
    }

    def __init__(self):
        from ctypes import (
            CFUNCTYPE,
            POINTER,
            Structure,
            byref,
            c_int32,
            c_uint32,
            c_void_p,
        )

        carbon = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/Carbon.framework/Carbon"
        )

        class EventTypeSpec(Structure):
            _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]

        class EventHotKeyID(Structure):
            _fields_ = [("signature", c_uint32), ("id", c_uint32)]

        self._byref = byref
        self._c_void_p = c_void_p
        self._EventHotKeyID = EventHotKeyID
        self._EventTypeSpec = EventTypeSpec
        self._carbon = carbon
        self._refs: Dict[int, c_void_p] = {}
        self._callbacks: Dict[int, Callback] = {}
        self._native_to_id: Dict[Tuple[int, int], int] = {}
        self._last_fire: Dict[int, float] = {}
        self._handler_ref = c_void_p()
        self._filter = None
        self._signature = _fourcc(b"HS2K")

        carbon.GetApplicationEventTarget.restype = c_void_p
        carbon.GetApplicationEventTarget.argtypes = []
        carbon.GetEventDispatcherTarget.restype = c_void_p
        carbon.GetEventDispatcherTarget.argtypes = []
        carbon.InstallEventHandler.restype = c_int32
        carbon.InstallEventHandler.argtypes = [
            c_void_p,
            c_void_p,
            c_uint32,
            POINTER(EventTypeSpec),
            c_void_p,
            POINTER(c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = c_int32
        carbon.RegisterEventHotKey.argtypes = [
            c_uint32,
            c_uint32,
            EventHotKeyID,
            c_void_p,
            c_uint32,
            POINTER(c_void_p),
        ]
        carbon.UnregisterEventHotKey.restype = c_int32
        carbon.UnregisterEventHotKey.argtypes = [c_void_p]
        carbon.GetEventParameter.restype = c_int32
        carbon.GetEventParameter.argtypes = [
            c_void_p,
            c_uint32,
            c_uint32,
            POINTER(c_uint32),
            c_uint32,
            POINTER(c_uint32),
            c_void_p,
        ]

        self._target = carbon.GetEventDispatcherTarget() or carbon.GetApplicationEventTarget()
        if not self._target:
            logger.error("macOS hotkeys: no Carbon event target (dispatcher and app both NULL)")
        else:
            logger.info(
                "macOS Carbon event target=%s (dispatcher=%s app=%s)",
                hex(int(self._target)),
                hex(int(carbon.GetEventDispatcherTarget() or 0)),
                hex(int(carbon.GetApplicationEventTarget() or 0)),
            )

        kEventClassKeyboard = _fourcc(b"keyb")
        kEventHotKeyPressed = 5
        # Keep the spec alive — some Carbon builds do not copy it.
        self._press_spec = EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)

        EventHandlerProcPtr = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)
        self._c_handler = EventHandlerProcPtr(self._on_carbon_event)

        if self._target:
            err = carbon.InstallEventHandler(
                self._target,
                self._c_handler,
                1,
                ctypes.byref(self._press_spec),
                None,
                ctypes.byref(self._handler_ref),
            )
            if err != 0:
                logger.error(f"InstallEventHandler(dispatcher) failed: {err}")
            else:
                logger.info("macOS Carbon hotkey handler installed on dispatcher target")

        # QHotkey uses the application target; install there too if it differs.
        app_target = carbon.GetApplicationEventTarget()
        self._handler_ref_app = c_void_p()
        if app_target and int(app_target) != int(self._target or 0):
            err = carbon.InstallEventHandler(
                app_target,
                self._c_handler,
                1,
                ctypes.byref(self._press_spec),
                None,
                ctypes.byref(self._handler_ref_app),
            )
            if err != 0:
                logger.warning(f"InstallEventHandler(app) failed: {err}")
            else:
                logger.info("macOS Carbon hotkey handler also installed on application target")

        self._install_qt_filter()

    def _install_qt_filter(self) -> None:
        """Second path: Qt sees NSEvents that Carbon's Python callback may miss."""
        try:
            from PySide6.QtCore import QAbstractEventDispatcher, QAbstractNativeEventFilter

            class _Filter(QAbstractNativeEventFilter):
                def __init__(self, owner: "_MacHotkeys"):
                    super().__init__()
                    self._owner = owner

                def nativeEventFilter(self, eventType, message):
                    try:
                        if isinstance(eventType, bytes):
                            et = eventType
                        else:
                            try:
                                et = bytes(eventType)
                            except Exception:
                                et = str(eventType)
                        if b"mac_generic_NSEvent" in et or "mac_generic_NSEvent" in str(et):
                            self._owner._on_nsevent(int(message))
                    except Exception as e:
                        logger.error(f"NSEvent filter error: {e}", exc_info=True)
                    return False

            disp = QAbstractEventDispatcher.instance()
            if disp is None:
                logger.warning("No Qt event dispatcher yet; NSEvent filter not installed")
                return
            self._filter = _Filter(self)
            disp.installNativeEventFilter(self._filter)
            logger.info("macOS NSEvent native filter installed")
        except Exception as e:
            logger.warning(f"Could not install NSEvent filter: {e}")

    def _mod(self, name: str) -> int:
        bits = 0
        for part in name.lower().replace(" ", "").split("+"):
            if part in ("ctrl", "control"):
                bits |= self.controlKey
            elif part in ("cmd", "command", "meta"):
                bits |= self.cmdKey
            elif part in ("alt", "option"):
                bits |= self.optionKey
            elif part == "shift":
                bits |= self.shiftKey
        return bits

    def register(self, hk_id: int, mod: str, key: str, callback: Callback,
                 repeat: bool = False) -> bool:
        kc = self._KC.get(key.lower())
        if kc is None:
            logger.warning(f"Unknown hotkey key: {key}")
            return False
        mods = self._mod(mod)
        if not self._target:
            logger.error("Cannot register hotkey; Carbon target is NULL")
            return False
        hkid = self._EventHotKeyID(self._signature, hk_id)
        ref = self._c_void_p()
        err = self._carbon.RegisterEventHotKey(
            kc,
            mods,
            hkid,
            self._target,
            0,
            self._byref(ref),
        )
        if err != 0:
            logger.warning(f"RegisterEventHotKey failed for {mod}+{key} (err={err})")
            return False
        self._refs[hk_id] = ref
        self._callbacks[hk_id] = callback
        self._native_to_id[(kc, mods)] = hk_id
        logger.info(f"Registered macOS hotkey ID {hk_id} ({mod}+{key}, keycode={kc})")
        return True

    def unregister(self, hk_id: int) -> None:
        ref = self._refs.pop(hk_id, None)
        self._callbacks.pop(hk_id, None)
        self._native_to_id = {k: v for k, v in self._native_to_id.items() if v != hk_id}
        if ref:
            self._carbon.UnregisterEventHotKey(ref)
            logger.debug(f"Unregistered macOS hotkey ID {hk_id}")

    def _fire(self, hk_id: int, source: str) -> None:
        now = time.monotonic()
        if now - self._last_fire.get(hk_id, 0) < 0.08:
            return
        cb = self._callbacks.get(hk_id)
        if not cb:
            return
        self._last_fire[hk_id] = now
        logger.info(f"Hotkey {hk_id} fired ({source})")
        cb()

    def _on_carbon_event(self, next_handler, event, user_data) -> int:
        try:
            logger.debug("Carbon hotkey event received")
            hkid = self._EventHotKeyID()
            err = self._carbon.GetEventParameter(
                event,
                _fourcc(b"----"),
                _fourcc(b"hkid"),
                None,
                ctypes.sizeof(hkid),
                None,
                self._byref(hkid),
            )
            if err == 0:
                self._fire(hkid.id, "carbon")
            else:
                logger.warning(f"GetEventParameter failed: {err}")
        except Exception as e:
            logger.error(f"macOS Carbon hotkey callback error: {e}", exc_info=True)
        return 0

    def _on_nsevent(self, ns_event: int) -> None:
        if not ns_event:
            return
        try:
            from ctypes import c_long, c_short, c_ushort, c_ulong
            from utils.macos_window import _o

            o = _o()
            etype = o.send(ns_event, "type", restype=c_ulong) or 0
            if etype == self.NSEventTypeSystemDefined:
                subtype = o.send(ns_event, "subtype", restype=c_short) or 0
                if subtype == 6:
                    data1 = int(o.send(ns_event, "data1", restype=c_long) or 0)
                    candidates = (
                        data1,
                        data1 & 0xFFFFFFFF,
                        (data1 >> 16) & 0xFFFF,
                        data1 & 0xFFFF,
                    )
                    for cand in candidates:
                        if cand in self._callbacks:
                            self._fire(int(cand), "nsevent-systemDefined")
                            return
            elif etype == self.NSEventTypeKeyDown:
                flags = int(o.send(ns_event, "modifierFlags", restype=c_ulong) or 0)
                keycode = int(o.send(ns_event, "keyCode", restype=c_ushort) or 0)
                carbon_mods = 0
                indep = flags & self.NSDeviceIndependentFlagsMask
                if indep & self.NSEventModifierFlagControl:
                    carbon_mods |= self.controlKey
                if indep & self.NSEventModifierFlagCommand:
                    carbon_mods |= self.cmdKey
                if indep & self.NSEventModifierFlagShift:
                    carbon_mods |= self.shiftKey
                if indep & self.NSEventModifierFlagOption:
                    carbon_mods |= self.optionKey
                hk_id = self._native_to_id.get((keycode, carbon_mods))
                if hk_id is not None:
                    self._fire(hk_id, "nsevent-keyDown")
        except Exception as e:
            logger.debug(f"NSEvent parse skipped: {e}")
