"""
KDE Plasma (KWin) window control for Wayland.

Wayland gives clients no way to stay on top, position themselves, or opt
out of screen capture. KWin can do all three, so we keep one KWin script
loaded for this process that, for every window we own (now and later):

- keepAbove = true                     (always on top)
- excludeFromCapture = <privacy flag>  (absent from screenshots / screencasts)
- skip taskbar / pager / Alt+Tab
- places the main overlay at the position we track here

Limitation: KWin stacks an *active fullscreen* window (e.g. a game) above
every keepAbove window, and scripts cannot change a window's layer.

Changing a setting reloads the script; KWin applies it within a frame.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import tempfile
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_KWIN_BUS = "org.kde.KWin"

_SCRIPT = """
const PID = %(pid)d;
const EXCLUDE = %(exclude)s;
const POS = %(positions)s;   // { "<window caption>": [x, y], ... }

function apply(w) {
    if (!w || w.pid !== PID) return;
    w.keepAbove = true;
    w.skipTaskbar = true;
    w.skipPager = true;
    w.skipSwitcher = true;
    w.excludeFromCapture = EXCLUDE;
    const target = POS[w.caption];
    if (target) {
        // KWin re-centers on-screen-display windows whenever they resize
        // (the overlay grows when screenshots are added), so pin each one.
        const pin = () => {
            const g = w.frameGeometry;
            if (Math.abs(g.x - target[0]) > 0.5 || Math.abs(g.y - target[1]) > 0.5)
                w.frameGeometry = {x: target[0], y: target[1], width: g.width, height: g.height};
        };
        w.frameGeometryChanged.connect(pin);
        pin();
    }
}

workspace.windowList().forEach(apply);
workspace.windowAdded.connect(apply);
"""


def kde_primary_output() -> Optional[str]:
    """Name of the output KDE treats as primary (priority 1).

    Qt's Wayland backend does not receive KDE's output order, so
    QGuiApplication.primaryScreen() is just the first output it saw."""
    import shutil
    import subprocess

    exe = shutil.which("kscreen-doctor")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-j"], capture_output=True, text=True, timeout=3).stdout
        outputs = [o for o in json.loads(out).get("outputs", []) if o.get("enabled")]
        ranked = sorted(
            (o for o in outputs if isinstance(o.get("priority"), int) and o["priority"] > 0),
            key=lambda o: o["priority"],
        )
        return ranked[0]["name"] if ranked else None
    except Exception as e:
        logger.debug(f"Could not read KDE output priority: {e}")
        return None


def kwin_wayland_available() -> bool:
    if not os.environ.get("WAYLAND_DISPLAY"):
        return False
    return "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")


class KWinWindowRules:
    def __init__(self):
        from jeepney.io.blocking import open_dbus_connection

        self._conn = open_dbus_connection(bus="SESSION")
        self._pid = os.getpid()
        self._name = f"hindsight-{self._pid}"
        self._exclude = True
        # caption -> (x, y); one entry per window we position (overlay, panel…)
        self._positions: Dict[str, Tuple[int, int]] = {}
        self._dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self._cleanup_stale()
        fd, self._path = tempfile.mkstemp(prefix="hindsight-kwin-", suffix=".js", dir=self._dir)
        os.close(fd)
        atexit.register(self.close)

    def _cleanup_stale(self) -> None:
        """Unload scripts left behind by instances that were killed or crashed
        (atexit never ran for them)."""
        import glob
        import re

        for path in glob.glob(os.path.join(self._dir, "hindsight-kwin-*.js")):
            try:
                with open(path, encoding="utf-8") as f:
                    m = re.search(r"const PID = (\d+);", f.read())
                pid = int(m.group(1)) if m else None
                if pid is not None and pid != self._pid:
                    try:
                        os.kill(pid, 0)
                        continue  # that instance is still running
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        continue
                    self._call("/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s",
                               (f"hindsight-{pid}",))
                os.unlink(path)
                logger.debug(f"Removed stale KWin script {path}")
            except Exception as e:
                logger.debug(f"Stale KWin script cleanup skipped {path}: {e}")

    # -- settings ------------------------------------------------------

    def window_position(self, caption: str) -> Optional[Tuple[int, int]]:
        return self._positions.get(caption)

    def set_exclude_from_capture(self, enabled: bool) -> bool:
        self._exclude = enabled
        return self._reload()

    def set_window_position(self, caption: str, x: int, y: int) -> bool:
        if not caption:
            return False
        self._positions[caption] = (int(x), int(y))
        return self._reload()

    def forget_window(self, caption: str) -> bool:
        if self._positions.pop(caption, None) is None:
            return True
        return self._reload()

    # -- D-Bus ---------------------------------------------------------

    def _call(self, path: str, iface: str, method: str, sig: str = "", body: tuple = ()):
        from jeepney import DBusAddress, new_method_call
        from jeepney.wrappers import unwrap_msg

        addr = DBusAddress(path, bus_name=_KWIN_BUS, interface=iface)
        return unwrap_msg(self._conn.send_and_get_reply(
            new_method_call(addr, method, sig, body), timeout=5
        ))

    def _reload(self) -> bool:
        src = _SCRIPT % {
            "pid": self._pid,
            "exclude": "true" if self._exclude else "false",
            "positions": json.dumps({c: list(p) for c, p in self._positions.items()}),
        }
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(src)
            self._call("/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s", (self._name,))
            (script_id,) = self._call(
                "/Scripting", "org.kde.kwin.Scripting", "loadScript", "ss", (self._path, self._name)
            )
            if script_id < 0:
                raise RuntimeError("KWin refused to load the window script")
            self._call(f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run")
            logger.debug(
                f"KWin rules applied: keepAbove, excludeFromCapture={self._exclude}, "
                f"positions={self._positions}"
            )
            return True
        except Exception as e:
            logger.error(f"KWin window rules failed: {e}")
            return False

    def close(self) -> None:
        try:
            self._call("/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s", (self._name,))
        except Exception:
            pass
        try:
            os.unlink(self._path)
        except OSError:
            pass
