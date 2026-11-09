"""
Put a Qt window in KWin's on-screen-display (OSD) layer on KDE Wayland.

KWin stacks an active fullscreen window (e.g. a game) above every
keepAbove window, but below OSD windows such as the volume popup.
A client opts into that role with the KDE `org_kde_plasma_shell` Wayland
protocol: org_kde_plasma_surface.set_role(onscreendisplay).

PySide does not expose a window's wl_surface, so we ask Qt's Wayland
plugin for it (QWaylandNativeInterface::nativeResourceForWindow, the same
call QPlatformNativeInterface users make from C++) and speak the protocol
through libwayland-client with ctypes, on a private event queue so we never
interfere with Qt's own dispatching.
"""

from __future__ import annotations

import ctypes
import logging
import os
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# org_kde_plasma_surface requests / enums (plasma-shell.xml)
_SURFACE_DESTROY = 0
_SURFACE_SET_ROLE = 3
_SURFACE_SET_SKIP_TASKBAR = 5
_SURFACE_SET_SKIP_SWITCHER = 9
_ROLE_ONSCREENDISPLAY = 3
_SHELL_GET_SURFACE = 0
_SHELL_VERSION = 8

_WL_DISPLAY_GET_REGISTRY = 1
_WL_REGISTRY_BIND = 0
_WL_MARSHAL_FLAG_DESTROY = 1


class _wl_message(ctypes.Structure):
    pass


class _wl_interface(ctypes.Structure):
    pass


_wl_message._fields_ = [
    ("name", ctypes.c_char_p),
    ("signature", ctypes.c_char_p),
    ("types", ctypes.POINTER(ctypes.POINTER(_wl_interface))),
]
_wl_interface._fields_ = [
    ("name", ctypes.c_char_p),
    ("version", ctypes.c_int),
    ("method_count", ctypes.c_int),
    ("methods", ctypes.POINTER(_wl_message)),
    ("event_count", ctypes.c_int),
    ("events", ctypes.POINTER(_wl_message)),
]


class _QByteArray(ctypes.Structure):
    """Qt 6 QByteArray (QArrayDataPointer<char>); d=NULL means raw, unowned data."""

    _fields_ = [("d", ctypes.c_void_p), ("ptr", ctypes.c_char_p), ("size", ctypes.c_ssize_t)]

    @classmethod
    def raw(cls, data: bytes) -> "_QByteArray":
        return cls(None, data, len(data))


_GLOBAL_FN = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32
)
_GLOBAL_REMOVE_FN = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)


class _RegistryListener(ctypes.Structure):
    _fields_ = [("global_", _GLOBAL_FN), ("global_remove", _GLOBAL_REMOVE_FN)]


def _messages(specs, keep):
    """Build a wl_message[] from (name, signature, [interface ptr or None per arg])."""
    arr = (_wl_message * max(1, len(specs)))()
    for i, (name, sig, types) in enumerate(specs):
        t = (ctypes.POINTER(_wl_interface) * max(1, len(types)))(*types)
        keep.append(t)
        arr[i] = _wl_message(name.encode(), sig.encode(), t)
    return arr


class KdeOsdLayer:
    """Promotes Qt windows to KWin's OSD layer. One instance per process."""

    def __init__(self):
        import PySide6

        qt_lib = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "lib")
        gui = ctypes.CDLL(os.path.join(qt_lib, "libQt6Gui.so.6"))
        wlc = ctypes.CDLL(os.path.join(qt_lib, "libQt6WaylandClient.so.6"))
        self._wl = wl = ctypes.CDLL("libwayland-client.so.0")

        gui._ZN15QGuiApplication23platformNativeInterfaceEv.restype = ctypes.c_void_p
        self._native = gui._ZN15QGuiApplication23platformNativeInterfaceEv()
        if not self._native:
            raise RuntimeError("Qt has no platform native interface")
        self._res_window = wlc._ZN15QtWaylandClient23QWaylandNativeInterface23nativeResourceForWindowERK10QByteArrayP7QWindow
        self._res_window.restype = ctypes.c_void_p
        self._res_window.argtypes = [ctypes.c_void_p, ctypes.POINTER(_QByteArray), ctypes.c_void_p]
        res_integration = wlc._ZN15QtWaylandClient23QWaylandNativeInterface28nativeResourceForIntegrationERK10QByteArray
        res_integration.restype = ctypes.c_void_p
        res_integration.argtypes = [ctypes.c_void_p, ctypes.POINTER(_QByteArray)]

        self._display = res_integration(self._native, ctypes.byref(_QByteArray.raw(b"wl_display")))
        if not self._display:
            raise RuntimeError("Qt is not running on Wayland")

        vp, u32 = ctypes.c_void_p, ctypes.c_uint32
        wl.wl_display_create_queue.restype = vp
        wl.wl_display_create_queue.argtypes = [vp]
        wl.wl_proxy_create_wrapper.restype = vp
        wl.wl_proxy_create_wrapper.argtypes = [vp]
        wl.wl_proxy_wrapper_destroy.argtypes = [vp]
        wl.wl_proxy_set_queue.argtypes = [vp, vp]
        wl.wl_proxy_get_version.restype = u32
        wl.wl_proxy_get_version.argtypes = [vp]
        wl.wl_proxy_add_listener.argtypes = [vp, vp, vp]
        wl.wl_display_roundtrip_queue.argtypes = [vp, vp]
        wl.wl_display_flush.argtypes = [vp]
        wl.wl_proxy_marshal_flags.restype = vp  # variadic: extra args typed per call

        # Interfaces libwayland-client already defines
        self._wl_registry_iface = _wl_interface.in_dll(wl, "wl_registry_interface")
        wl_surface_iface = _wl_interface.in_dll(wl, "wl_surface_interface")
        wl_output_iface = _wl_interface.in_dll(wl, "wl_output_interface")

        # org_kde_plasma_surface / org_kde_plasma_shell, from plasma-shell.xml
        self._keep = []
        ps = self._plasma_surface_iface = _wl_interface()
        none = ctypes.POINTER(_wl_interface)()
        surf_methods = _messages([
            ("destroy", "", []),
            ("set_output", "o", [ctypes.pointer(wl_output_iface)]),
            ("set_position", "ii", [none, none]),
            ("set_role", "u", [none]),
            ("set_panel_behavior", "u", [none]),
            ("set_skip_taskbar", "2u", [none]),
            ("panel_auto_hide_hide", "4", []),
            ("panel_auto_hide_show", "4", []),
            ("set_panel_takes_focus", "4u", [none]),
            ("set_skip_switcher", "5u", [none]),
            ("open_under_cursor", "7", []),
        ], self._keep)
        surf_events = _messages([
            ("auto_hidden_panel_hidden", "4", []),
            ("auto_hidden_panel_shown", "4", []),
        ], self._keep)
        ps.name, ps.version = b"org_kde_plasma_surface", _SHELL_VERSION
        ps.method_count, ps.methods = 11, surf_methods
        ps.event_count, ps.events = 2, surf_events

        sh = self._plasma_shell_iface = _wl_interface()
        shell_methods = _messages([
            ("get_surface", "no", [ctypes.pointer(ps), ctypes.pointer(wl_surface_iface)]),
        ], self._keep)
        sh.name, sh.version = b"org_kde_plasma_shell", _SHELL_VERSION
        sh.method_count, sh.methods = 1, shell_methods
        sh.event_count, sh.events = 0, ctypes.POINTER(_wl_message)()

        self._queue = wl.wl_display_create_queue(self._display)
        self._shell: Optional[int] = None
        self._bind_shell()
        if not self._shell:
            raise RuntimeError("compositor does not offer org_kde_plasma_shell")
        # id(QWindow) -> (wl_surface, org_kde_plasma_surface proxy)
        self._surfaces: Dict[int, Tuple[int, int]] = {}

    def _marshal(self, proxy, opcode, iface, version, flags, *args):
        return self._wl.wl_proxy_marshal_flags(
            ctypes.c_void_p(proxy), ctypes.c_uint32(opcode),
            ctypes.cast(ctypes.pointer(iface), ctypes.c_void_p) if iface is not None else None,
            ctypes.c_uint32(version), ctypes.c_uint32(flags), *args,
        )

    def _bind_shell(self) -> None:
        wl = self._wl
        wrapper = wl.wl_proxy_create_wrapper(self._display)
        wl.wl_proxy_set_queue(wrapper, self._queue)
        registry = self._marshal(
            wrapper, _WL_DISPLAY_GET_REGISTRY, self._wl_registry_iface,
            wl.wl_proxy_get_version(wrapper), 0, ctypes.c_void_p(None),
        )
        wl.wl_proxy_wrapper_destroy(wrapper)

        found = {}

        def on_global(_data, _reg, name, interface, version):
            if interface == b"org_kde_plasma_shell":
                found["name"], found["version"] = name, version

        def on_remove(_data, _reg, _name):
            pass

        self._listener = _RegistryListener(_GLOBAL_FN(on_global), _GLOBAL_REMOVE_FN(on_remove))
        wl.wl_proxy_add_listener(registry, ctypes.cast(ctypes.byref(self._listener), ctypes.c_void_p), None)
        wl.wl_display_roundtrip_queue(self._display, self._queue)

        if "name" in found:
            version = min(found["version"], _SHELL_VERSION)
            self._shell = self._marshal(
                registry, _WL_REGISTRY_BIND, self._plasma_shell_iface, version, 0,
                ctypes.c_uint32(found["name"]), ctypes.c_char_p(b"org_kde_plasma_shell"),
                ctypes.c_uint32(version), ctypes.c_void_p(None),
            )
            self._shell_version = version
            wl.wl_display_flush(self._display)

    def _wl_surface(self, qwindow) -> Optional[int]:
        import shiboken6

        ptr = shiboken6.getCppPointer(qwindow)[0]
        return self._res_window(self._native, ctypes.byref(_QByteArray.raw(b"surface")), ptr) or None

    def apply(self, qwindow) -> bool:
        """Give the window's wl_surface the OSD role. Call before the window is
        first shown: KWin only re-layers a window when it gets mapped. Qt keeps
        the same wl_surface across hide/show, so once is enough; calling again
        is a no-op unless Qt recreated the surface."""
        surface = self._wl_surface(qwindow)
        if not surface:
            logger.debug("OSD layer: window has no wl_surface yet")
            return False
        key = id(qwindow)
        prev = self._surfaces.get(key)
        if prev and prev[0] == surface:
            return True
        if prev:
            self._marshal(prev[1], _SURFACE_DESTROY, None, self._shell_version, _WL_MARSHAL_FLAG_DESTROY)
        v = self._shell_version
        ps = self._marshal(
            self._shell, _SHELL_GET_SURFACE, self._plasma_surface_iface, v, 0,
            ctypes.c_void_p(None), ctypes.c_void_p(surface),
        )
        self._marshal(ps, _SURFACE_SET_ROLE, None, v, 0, ctypes.c_uint32(_ROLE_ONSCREENDISPLAY))
        if v >= 2:
            self._marshal(ps, _SURFACE_SET_SKIP_TASKBAR, None, v, 0, ctypes.c_uint32(1))
        if v >= 5:
            self._marshal(ps, _SURFACE_SET_SKIP_SWITCHER, None, v, 0, ctypes.c_uint32(1))
        self._wl.wl_display_flush(self._display)
        self._surfaces[key] = (surface, ps)
        logger.info("Overlay placed in KWin's on-screen-display layer (above fullscreen apps)")
        return True
