"""
Linux global hotkeys.

Both backends grab the chord *exclusively*: while Hindsight holds it, the
keypress is delivered only to Hindsight and never reaches the focused app
(e.g. Ctrl+R will not also reload the browser).

Wayland: xdg-desktop-portal GlobalShortcuts. The compositor (KWin, GNOME
         Shell 48+, Hyprland, ...) owns the grab and tells us via D-Bus.
         The first bind shows a system dialog to approve the shortcuts;
         they can be changed later in the desktop's shortcut settings.
X11:     XGrabKey on the root window, on a private Xlib connection.

Every backend calls back on a worker thread, so activations are marshalled
to the Qt main thread through a queued signal.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import queue
import secrets
import select
import threading
from typing import Callable, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)

Callback = Callable[[], None]

APP_ID = "io.github.gnavadev.Hindsight"

_DESCRIPTIONS = {
    "h": "Take screenshot",
    "b": "Show / hide overlay",
    "p": "Toggle privacy mode",
    "r": "Reset",
    "q": "Quit Hindsight",
    "]": "Record PC audio (start / stop)",
    "up": "Move overlay up",
    "down": "Move overlay down",
    "left": "Move overlay left",
    "right": "Move overlay right",
    "enter": "Send to LLM",
    "return": "Send to LLM",
}

# xkb keysym names (used by both the portal trigger syntax and XStringToKeysym)
_KEYSYMS = {
    "h": "h",
    "b": "b",
    "p": "p",
    "r": "r",
    "q": "q",
    "]": "bracketright",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "enter": "Return",
    "return": "Return",
}


def _mod_parts(mod: str) -> List[str]:
    out = []
    for part in mod.lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            out.append("ctrl")
        elif part == "shift":
            out.append("shift")
        elif part in ("alt", "option"):
            out.append("alt")
        elif part in ("cmd", "command", "meta", "super", "win", "logo"):
            out.append("logo")
    return out


class _Bridge(QObject):
    """Lives on the GUI thread; worker threads emit `fired` / `released`,
    slots run on the GUI thread.

    Hotkeys in `repeating` keep firing while held (the portal reports one
    press and one release, with no key repeat in between)."""

    fired = Signal(int)
    released = Signal(int)

    REPEAT_DELAY_MS = 250     # like keyboard auto-repeat: pause, then repeat
    REPEAT_INTERVAL_MS = 30
    REPEAT_MAX_S = 15         # safety stop if a release is ever lost

    def __init__(self):
        super().__init__()
        from PySide6.QtCore import QTimer

        self.callbacks: Dict[int, Callback] = {}
        self.repeating: Set[int] = set()
        self._held: Optional[int] = None
        self._held_since = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_repeat)
        self.fired.connect(self._dispatch)
        self.released.connect(self._on_release)

    def _call(self, hk_id: int) -> None:
        cb = self.callbacks.get(hk_id)
        if cb is None:
            return
        try:
            cb()
        except Exception as e:
            logger.error(f"Hotkey {hk_id} callback failed: {e}", exc_info=True)

    @Slot(int)
    def _dispatch(self, hk_id: int) -> None:
        if hk_id not in self.callbacks:
            return
        logger.info(f"Hotkey {hk_id} fired")
        self._call(hk_id)
        if hk_id in self.repeating:
            import time
            self._held, self._held_since = hk_id, time.monotonic()
            self._timer.start(self.REPEAT_DELAY_MS)
        elif self._held is not None:
            self._stop()

    @Slot()
    def _on_repeat(self) -> None:
        import time
        if self._held is None or time.monotonic() - self._held_since > self.REPEAT_MAX_S:
            self._stop()
            return
        self._timer.setInterval(self.REPEAT_INTERVAL_MS)
        self._call(self._held)

    @Slot(int)
    def _on_release(self, hk_id: int) -> None:
        if hk_id == self._held:
            self._stop()

    def _stop(self) -> None:
        self._timer.stop()
        self._held = None


# ── Wayland: xdg-desktop-portal GlobalShortcuts ──────────────────────


class PortalHotkeys:
    BUS = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.GlobalShortcuts"
    BIND_TIMEOUT = 600  # the user may sit on the approval dialog

    _PORTAL_MODS = {"ctrl": "CTRL", "shift": "SHIFT", "alt": "ALT", "logo": "LOGO"}

    def __init__(self):
        self._bridge = _Bridge()
        self._ops: "queue.Queue[tuple]" = queue.Queue()
        # Owned by the worker thread; read (atomically) by the signal thread.
        self._sessions: Dict[str, Set[int]] = {}
        self._router = None
        threading.Thread(target=self._worker, name="portal-hotkeys", daemon=True).start()

    # -- public API (GUI thread) --------------------------------------

    def register(self, hk_id: int, mod: str, key: str, callback: Callback,
                 repeat: bool = False) -> bool:
        sym = _KEYSYMS.get(key.lower())
        if sym is None:
            logger.warning(f"Unknown hotkey key: {key}")
            return False
        mods = [self._PORTAL_MODS[m] for m in _mod_parts(mod)]
        trigger = "+".join(mods + [sym])
        desc = _DESCRIPTIONS.get(key.lower(), f"{mod}+{key}")
        self._bridge.callbacks[hk_id] = callback
        if repeat:
            self._bridge.repeating.add(hk_id)
        self._ops.put(("bind", hk_id, trigger, desc))
        return True

    def unregister(self, hk_id: int) -> None:
        if self._bridge.callbacks.pop(hk_id, None) is not None:
            self._ops.put(("unbind", hk_id))

    def handle_native_event(self, event_type, message):
        return None

    # -- worker thread -------------------------------------------------

    def _worker(self) -> None:
        try:
            from jeepney import DBusAddress, MatchRule, new_method_call
            from jeepney.bus_messages import message_bus
            from jeepney.io.threading import DBusRouter, open_dbus_connection
            from jeepney.wrappers import unwrap_msg
        except ImportError:
            logger.error("Wayland hotkeys need the 'jeepney' package (pip install jeepney)")
            return

        self._new_method_call = new_method_call
        self._MatchRule = MatchRule
        self._unwrap = unwrap_msg
        self._portal = DBusAddress(self.PATH, bus_name=self.BUS, interface=self.IFACE)

        try:
            self._router = DBusRouter(open_dbus_connection(bus="SESSION"))
            self._register_app_id(DBusAddress)

            version = self._portal_version(DBusAddress)
            if version is None:
                logger.error(
                    "Global hotkeys unavailable: this desktop's xdg-desktop-portal "
                    "has no GlobalShortcuts support"
                )
                return
            logger.info(f"Using xdg-desktop-portal GlobalShortcuts v{version} for hotkeys")

            # Activated = key pressed, Deactivated = key released (both from the
            # same queue, so they stay in order)
            signals: "queue.Queue" = queue.Queue(maxsize=256)
            self._filters = []
            for member in ("Activated", "Deactivated"):
                rule = MatchRule(
                    type="signal", interface=self.IFACE, member=member, path=self.PATH
                )
                self._unwrap(self._router.send_and_get_reply(message_bus.AddMatch(rule)))
                self._filters.append(self._router.filter(rule, queue=signals))
            threading.Thread(
                target=self._signal_loop, args=(signals,),
                name="portal-hotkeys-signals", daemon=True,
            ).start()
        except Exception as e:
            logger.error(f"Could not connect to the hotkey portal: {e}", exc_info=True)
            return

        pending: Optional[tuple] = None
        while True:
            op = pending or self._ops.get()
            pending = None
            try:
                if op[0] == "bind":
                    batch = [op]
                    # register() is called in a burst at startup: bind as one session
                    while True:
                        try:
                            nxt = self._ops.get(timeout=0.15)
                        except queue.Empty:
                            break
                        if nxt[0] != "bind":
                            pending = nxt
                            break
                        batch.append(nxt)
                    self._bind(batch)
                else:
                    self._unbind(op[1])
            except Exception as e:
                logger.error(f"Hotkey portal operation {op[0]} failed: {e}", exc_info=True)

    def _register_app_id(self, DBusAddress) -> None:
        """Tell the portal who we are, so approved shortcuts persist across runs.
        Must precede any other portal call on this connection."""
        ensure_desktop_entry()
        reg = DBusAddress(self.PATH, bus_name=self.BUS,
                          interface="org.freedesktop.host.portal.Registry")
        try:
            self._unwrap(self._router.send_and_get_reply(
                self._new_method_call(reg, "Register", "sa{sv}", (APP_ID, {})), timeout=5
            ))
            logger.debug(f"Registered with portal as {APP_ID}")
        except Exception as e:
            logger.debug(f"Portal app-id registration unavailable: {e}")

    def _portal_version(self, DBusAddress) -> Optional[int]:
        props = DBusAddress(self.PATH, bus_name=self.BUS,
                            interface="org.freedesktop.DBus.Properties")
        try:
            reply = self._unwrap(self._router.send_and_get_reply(
                self._new_method_call(props, "Get", "ss", (self.IFACE, "version")), timeout=5
            ))
            return int(reply[0][1])
        except Exception as e:
            logger.debug(f"GlobalShortcuts version query failed: {e}")
            return None

    def _request(self, method: str, signature: str, args: tuple, timeout: float) -> Dict:
        """Call a portal method that answers via an org.freedesktop.portal.Request."""
        token = "hs" + secrets.token_hex(8)
        sender = self._router.unique_name.lstrip(":").replace(".", "_")
        req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        options = dict(args[-1])
        options["handle_token"] = ("s", token)
        args = args[:-1] + (options,)

        # Subscribe before calling, or a fast portal can answer before we listen.
        rule = self._MatchRule(type="signal", interface="org.freedesktop.portal.Request",
                               member="Response", path=req_path)
        from jeepney.bus_messages import message_bus
        self._unwrap(self._router.send_and_get_reply(message_bus.AddMatch(rule)))
        try:
            with self._router.filter(rule) as responses:
                self._unwrap(self._router.send_and_get_reply(
                    self._new_method_call(self._portal, method, signature, args), timeout=10
                ))
                try:
                    code, results = responses.get(timeout=timeout).body
                except queue.Empty:
                    raise TimeoutError(f"portal {method} gave no response")
        finally:
            self._router.send(message_bus.RemoveMatch(rule))
        if code != 0:
            raise RuntimeError(f"portal {method} {'cancelled' if code == 1 else 'failed'}")
        return {k: v[1] for k, v in results.items()}

    def _bind(self, batch: List[tuple]) -> None:
        live = set(self._bridge.callbacks)
        batch = [b for b in batch if b[1] in live and not self._session_of(b[1])]
        if not batch:
            return

        session = self._request(
            "CreateSession", "a{sv}",
            ({"session_handle_token": ("s", "hs" + secrets.token_hex(8))},), timeout=10,
        )["session_handle"]

        shortcuts = [
            (f"hk{hk_id}", {"description": ("s", desc), "preferred_trigger": ("s", trigger)})
            for _, hk_id, trigger, desc in batch
        ]
        try:
            results = self._request(
                "BindShortcuts", "oa(sa{sv})sa{sv}", (session, shortcuts, "", {}),
                timeout=self.BIND_TIMEOUT,
            )
        except Exception:
            self._close_session(session)
            raise

        self._sessions[session] = {hk_id for _, hk_id, _, _ in batch}
        for sid, info in results.get("shortcuts", []):
            trig = info.get("trigger_description", ("s", "?"))[1] or "(unassigned)"
            logger.info(f"Registered Wayland hotkey {sid}: {trig}")

        # Unregistered while we were waiting for the user to approve
        for _, hk_id, _, _ in batch:
            if hk_id not in self._bridge.callbacks:
                self._unbind(hk_id)

    def _session_of(self, hk_id: int) -> Optional[str]:
        for s, ids in self._sessions.items():
            if hk_id in ids:
                return s
        return None

    def _unbind(self, hk_id: int) -> None:
        session = self._session_of(hk_id)
        if session is None:
            return
        ids = self._sessions[session]
        ids.discard(hk_id)
        if not ids:
            # Closing the session releases the grab, so the key reaches other apps again.
            del self._sessions[session]
            self._close_session(session)
            logger.debug(f"Released Wayland hotkey hk{hk_id}")

    def _close_session(self, session: str) -> None:
        from jeepney import DBusAddress
        addr = DBusAddress(session, bus_name=self.BUS, interface="org.freedesktop.portal.Session")
        try:
            self._unwrap(self._router.send_and_get_reply(
                self._new_method_call(addr, "Close"), timeout=5
            ))
        except Exception as e:
            logger.debug(f"Closing portal session {session} failed: {e}")

    def _signal_loop(self, q: "queue.Queue") -> None:
        while True:
            msg = q.get()
            try:
                session, sid = msg.body[0], msg.body[1]
                ids = self._sessions.get(session)
                if ids is None or not sid.startswith("hk"):
                    continue
                hk_id = int(sid[2:])
                if hk_id in ids:
                    from jeepney import HeaderFields
                    member = msg.header.fields.get(HeaderFields.member)
                    if member == "Deactivated":
                        self._bridge.released.emit(hk_id)
                    else:
                        self._bridge.fired.emit(hk_id)
            except Exception as e:
                logger.error(f"Bad portal shortcut signal: {e}", exc_info=True)


# ── X11: XGrabKey ────────────────────────────────────────────────────


class _XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xkey", _XKeyEvent), ("pad", ctypes.c_long * 24)]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


class X11Hotkeys:
    KeyPress = 2
    GrabModeAsync = 1
    _MASKS = {"shift": 1 << 0, "ctrl": 1 << 2, "alt": 1 << 3, "logo": 1 << 6}
    _LOCK, _NUMLOCK = 1 << 1, 1 << 4
    _RELEVANT = (1 << 0) | (1 << 2) | (1 << 3) | (1 << 6)

    def __init__(self):
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        x = ctypes.cdll.LoadLibrary(name)
        vp, ul, ui, i = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_int
        x.XOpenDisplay.restype = vp
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ul
        x.XDefaultRootWindow.argtypes = [vp]
        x.XConnectionNumber.argtypes = [vp]
        x.XStringToKeysym.restype = ul
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [vp, ul]
        x.XGrabKey.argtypes = [vp, i, ui, ul, i, i, i]
        x.XUngrabKey.argtypes = [vp, i, ui, ul]
        x.XSync.argtypes = [vp, i]
        x.XPending.argtypes = [vp]
        x.XNextEvent.argtypes = [vp, ctypes.POINTER(_XEvent)]
        x.XFlush.argtypes = [vp]
        self._x = x

        self._dpy = x.XOpenDisplay(None)
        if not self._dpy:
            raise RuntimeError("cannot open X display")
        self._root = x.XDefaultRootWindow(self._dpy)

        # Xlib's default error handler exits the process; a BadAccess from a
        # chord another app already grabbed must only fail that one hotkey.
        self._grab_failed = False
        handler_t = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))

        def _on_error(_dpy, err):
            self._grab_failed = True
            return 0

        self._error_handler = handler_t(_on_error)  # keep alive
        x.XSetErrorHandler.argtypes = [handler_t]
        x.XSetErrorHandler(self._error_handler)

        self._bridge = _Bridge()
        self._grabs: Dict[Tuple[int, int], int] = {}  # (keycode, mods) -> hk_id
        self._ops: "queue.Queue[tuple]" = queue.Queue()
        self._results: "queue.Queue[bool]" = queue.Queue()
        self._wake_r, self._wake_w = os.pipe()
        threading.Thread(target=self._run, name="x11-hotkeys", daemon=True).start()
        logger.info("Using X11 XGrabKey for hotkeys")

    def register(self, hk_id: int, mod: str, key: str, callback: Callback,
                 repeat: bool = False) -> bool:
        sym = _KEYSYMS.get(key.lower())
        if sym is None:
            logger.warning(f"Unknown hotkey key: {key}")
            return False
        mods = 0
        for m in _mod_parts(mod):
            mods |= self._MASKS[m]
        ok = self._call(("grab", hk_id, sym, mods))
        if ok:
            self._bridge.callbacks[hk_id] = callback
            logger.info(f"Registered X11 hotkey ID {hk_id} ({mod}+{key})")
        else:
            logger.warning(f"X11 hotkey {mod}+{key} is already taken by another application")
        return ok

    def unregister(self, hk_id: int) -> None:
        if self._bridge.callbacks.pop(hk_id, None) is not None:
            self._call(("ungrab", hk_id))

    def handle_native_event(self, event_type, message):
        return None

    def _call(self, op: tuple) -> bool:
        """Run an Xlib op on the hotkey thread (the Display is not shared)."""
        self._ops.put(op)
        os.write(self._wake_w, b"x")
        try:
            return self._results.get(timeout=2)
        except queue.Empty:
            return False

    def _variants(self, mods: int):
        # Grab regardless of CapsLock / NumLock state
        for extra in (0, self._LOCK, self._NUMLOCK, self._LOCK | self._NUMLOCK):
            yield mods | extra

    def _do(self, op: tuple) -> bool:
        x, dpy, root = self._x, self._dpy, self._root
        if op[0] == "grab":
            _, hk_id, sym, mods = op
            code = x.XKeysymToKeycode(dpy, x.XStringToKeysym(sym.encode()))
            if not code:
                return False
            self._grab_failed = False
            for m in self._variants(mods):
                x.XGrabKey(dpy, code, m, root, 0, self.GrabModeAsync, self.GrabModeAsync)
            x.XSync(dpy, 0)
            if self._grab_failed:
                for m in self._variants(mods):
                    x.XUngrabKey(dpy, code, m, root)
                x.XSync(dpy, 0)
                return False
            self._grabs[(code, mods)] = hk_id
            return True
        _, hk_id = op
        for (code, mods), hid in list(self._grabs.items()):
            if hid == hk_id:
                for m in self._variants(mods):
                    x.XUngrabKey(dpy, code, m, root)
                del self._grabs[(code, mods)]
        x.XSync(dpy, 0)
        return True

    def _run(self) -> None:
        x, dpy = self._x, self._dpy
        fd = x.XConnectionNumber(dpy)
        ev = _XEvent()
        while True:
            try:
                while not self._ops.empty():
                    self._results.put(self._do(self._ops.get()))
                while x.XPending(dpy):
                    x.XNextEvent(dpy, ctypes.byref(ev))
                    if ev.type == self.KeyPress:
                        k = (ev.xkey.keycode, ev.xkey.state & self._RELEVANT)
                        hk_id = self._grabs.get(k)
                        if hk_id is not None:
                            self._bridge.fired.emit(hk_id)
                readable, _, _ = select.select([fd, self._wake_r], [], [], 1.0)
                if self._wake_r in readable:
                    os.read(self._wake_r, 4096)
            except Exception as e:
                logger.error(f"X11 hotkey loop error: {e}", exc_info=True)


def _launch_command() -> str:
    import shlex
    import sys

    if getattr(sys, "frozen", False):  # PyInstaller build
        return shlex.quote(os.path.abspath(sys.executable))
    main = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    return " ".join(shlex.quote(p) for p in (sys.executable, main) if p)


def ensure_desktop_entry() -> None:
    """The portal only accepts an app ID that has a .desktop file. Write one
    (also a menu launcher) if it is missing or points at another location."""
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(data_home, "applications", f"{APP_ID}.desktop")
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Hindsight\n"
        "Comment=Privacy LLM Assistant overlay\n"
        f"Exec={_launch_command()}\n"
        "Icon=utilities-terminal\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == content:
                return
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"Wrote desktop entry {path}")
    except OSError as e:
        logger.warning(f"Could not write desktop entry {path}: {e}")


def create_linux_hotkey_manager():
    """Pick the backend for the current session, or None if unsupported."""
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return PortalHotkeys()
    if os.environ.get("DISPLAY"):
        try:
            return X11Hotkeys()
        except Exception as e:
            logger.error(f"X11 hotkeys unavailable: {e}")
    return None
