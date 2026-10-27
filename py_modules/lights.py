"""Legion Go 2 stick RGB and power-button LED.

Stick rings: hid-lenovo-go sysfs ``/sys/class/leds/go:rgb:joystick_rings``.
Power button: LPBL in ERAM at 0xFEEC2310 bit 6, inverted (0 = on, 1 = off).
"""

from __future__ import annotations

import colorsys
import json
import logging
import mmap
import os
import threading
import time
from typing import Any, Callable, Protocol

LED_PATH = "/sys/class/leds/go:rgb:joystick_rings"
DMI_PRODUCT_PATH = "/sys/class/dmi/id/product_name"
GO2_PRODUCTS = frozenset({"83N0", "83N1"})
RGB_PROFILE = "3"
RGB_MODE = "custom"
EFFECTS = {
    "monocolor": "Solid",
    "breathe": "Breathing",
    "chroma": "Color cycle",
    "rainbow": "Spiral",
}
COLOR_EFFECTS = frozenset({"monocolor", "breathe"})
SPEED_EFFECTS = frozenset({"breathe", "chroma", "rainbow"})
REQUIRED_ATTRS = (
    "enabled",
    "profile",
    "mode",
    "effect",
    "brightness",
    "multi_intensity",
    "speed",
)
POWER_MMIO_BASE = 0xFEEC2300
POWER_OFFSET = 0x10
POWER_BIT = 6
DEV_MEM_PATH = "/dev/mem"
STARTUP_SETTLE_S = 30.0
RESUME_MIN_S = 1.0
DEFAULTS: dict[str, Any] = {
    "rings_enabled": True,
    "effect": "monocolor",
    "hue": 0,
    "saturation": 100,
    "brightness": 40,
    "speed": 66,
    "power_led_enabled": True,
    "power_led_sleep_off": True,
}

logger = logging.getLogger("legion-go2-lights")


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def sanitize_settings(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    effect = str(data.get("effect", DEFAULTS["effect"])).lower()
    if effect not in EFFECTS:
        effect = DEFAULTS["effect"]
    return {
        "rings_enabled": _as_bool(data.get("rings_enabled"), DEFAULTS["rings_enabled"]),
        "effect": effect,
        "hue": _clamp_int(data.get("hue"), DEFAULTS["hue"], 0, 359),
        "saturation": _clamp_int(data.get("saturation"), DEFAULTS["saturation"], 0, 100),
        "brightness": _clamp_int(data.get("brightness"), DEFAULTS["brightness"], 0, 100),
        "speed": _clamp_int(data.get("speed"), DEFAULTS["speed"], 0, 100),
        "power_led_enabled": _as_bool(
            data.get("power_led_enabled"), DEFAULTS["power_led_enabled"]
        ),
        "power_led_sleep_off": _as_bool(
            data.get("power_led_sleep_off"), DEFAULTS["power_led_sleep_off"]
        ),
    }


def read_text(path: str, limit: int = 256) -> str:
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        return handle.read(limit).strip()


def write_text(path: str, value: str) -> None:
    payload = (value.strip() + "\n").encode("ascii")
    flags = os.O_WRONLY | os.O_TRUNC
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


HW_TIMEOUT_S = 1.5


def run_with_timeout(func: Callable[[], Any], timeout: float = HW_TIMEOUT_S, default: Any = None) -> Any:
    """Run *func* in a daemon thread. If it does not finish, return *default*.

    hid-lenovo-go sysfs and /dev/mem can block in the kernel. The RPC loop
    must not wait on that.
    """
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = func()
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return default
    if "error" in box:
        raise box["error"]
    return box.get("value", default)


def dmi_product(path: str = DMI_PRODUCT_PATH) -> str:
    try:
        return read_text(path, 64)
    except OSError:
        return ""


def is_go2(product: str | None = None, led_path: str = LED_PATH) -> bool:
    name = dmi_product() if product is None else product
    return name in GO2_PRODUCTS or os.path.lexists(led_path)


def huesync_present(plugin_dir: str) -> bool:
    plugins = os.path.dirname(os.path.abspath(plugin_dir))
    try:
        names = os.listdir(plugins)
    except OSError:
        return False
    for name in names:
        if name.casefold() == "huesync":
            return True
        manifest = os.path.join(plugins, name, "plugin.json")
        try:
            with open(manifest, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if str(data.get("name", "")).casefold() == "huesync":
            return True
    return False


class Store(Protocol):
    def load(self) -> dict[str, Any]: ...
    def save(self, settings: dict[str, Any]) -> None: ...


class JsonStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return sanitize_settings(None)
        if isinstance(data, dict) and "settings" in data:
            data = data["settings"]
        return sanitize_settings(data)

    def save(self, settings: dict[str, Any]) -> None:
        clean = sanitize_settings(settings)
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"settings": clean}, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, self.path)


class PowerLed(Protocol):
    def available(self) -> bool: ...
    def get(self) -> bool | None: ...
    def set(self, enabled: bool) -> bool: ...


class MmioPowerLed:
    """Go 2 LPBL: bit 0 = on, bit 1 = off."""

    def __init__(
        self,
        base: int = POWER_MMIO_BASE,
        offset: int = POWER_OFFSET,
        bit: int = POWER_BIT,
        mem_path: str = DEV_MEM_PATH,
    ):
        self.base = base
        self.offset = offset
        self.mask = 1 << bit
        self.mem_path = mem_path

    def available(self) -> bool:
        if not os.path.exists(self.mem_path):
            return False
        return self.get() is not None

    def _map(self, write: bool, operation: Callable[[mmap.mmap, int], Any]) -> Any:
        address = self.base + self.offset
        page = mmap.PAGESIZE
        map_base = address - (address % page)
        page_offset = address - map_base
        flags = os.O_RDWR if write else os.O_RDONLY
        flags |= os.O_SYNC | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(self.mem_path, flags)
        try:
            protection = mmap.PROT_READ | (mmap.PROT_WRITE if write else 0)
            mapped = mmap.mmap(
                fd,
                page_offset + 1,
                flags=mmap.MAP_SHARED,
                prot=protection,
                offset=map_base,
            )
            try:
                return operation(mapped, page_offset)
            finally:
                mapped.close()
        finally:
            os.close(fd)

    def get(self) -> bool | None:
        def _read() -> bool:
            value = self._map(False, lambda mapped, offset: mapped[offset])
            return (value & self.mask) == 0

        try:
            return run_with_timeout(_read, timeout=0.8, default=None)
        except (OSError, ValueError):
            return None

    def set(self, enabled: bool) -> bool:
        mask = self.mask

        def _write() -> bool:
            def _mapped(mapped: mmap.mmap, offset: int) -> bool:
                before = mapped[offset]
                target = before & ~mask if enabled else before | mask
                if target != before:
                    mapped[offset] = target
                return (mapped[offset] & mask) == (target & mask)

            return bool(self._map(True, _mapped))

        try:
            return bool(run_with_timeout(_write, timeout=0.8, default=False))
        except (OSError, ValueError):
            return False


class MemoryPowerLed:
    """In-memory stand-in used by tests."""

    def __init__(self, initial_on: bool = True):
        self.byte = 0 if initial_on else (1 << POWER_BIT)
        self.mask = 1 << POWER_BIT
        self.ok = True

    def available(self) -> bool:
        return self.ok

    def get(self) -> bool | None:
        if not self.ok:
            return None
        return (self.byte & self.mask) == 0

    def set(self, enabled: bool) -> bool:
        if not self.ok:
            return False
        if enabled:
            self.byte &= ~self.mask
        else:
            self.byte |= self.mask
        return True


class ResumeWatch:
    """True once after the machine has spent at least a second suspended."""

    def __init__(self) -> None:
        self.last = self._offset()

    def mark(self) -> None:
        self.last = self._offset()

    def __call__(self) -> bool:
        current = self._offset()
        if current is None:
            return False
        previous, self.last = self.last, current
        return previous is not None and current - previous >= RESUME_MIN_S

    @staticmethod
    def _offset() -> float | None:
        clock = getattr(time, "CLOCK_BOOTTIME", None)
        if clock is None or not hasattr(time, "clock_gettime"):
            return None
        try:
            return time.clock_gettime(clock) - time.monotonic()
        except (OSError, ValueError):
            return None


def hsv_to_sysfs(hue: int, saturation: int, maxima: tuple[int, int, int]) -> str:
    red, green, blue = colorsys.hsv_to_rgb(hue / 360.0, saturation / 100.0, 1.0)
    values = [
        round(channel * maximum)
        for channel, maximum in zip((red, green, blue), maxima)
    ]
    return " ".join(str(value) for value in values)


class LightsController:
    def __init__(
        self,
        store: Store,
        led_path: str = LED_PATH,
        power_led: PowerLed | None = None,
        product: str | None = None,
        huesync: bool = False,
        log: logging.Logger | None = None,
        settle_s: float = STARTUP_SETTLE_S,
    ):
        self.store = store
        self.led_path = led_path
        self.power_led = power_led or MmioPowerLed()
        self.product = dmi_product() if product is None else product
        self.huesync = huesync
        self.log = log or logger
        self.settle_s = settle_s
        self.settings = sanitize_settings(store.load())
        self.lock = threading.RLock()
        self.resume_watch = ResumeWatch()
        self.suspended = False
        self._running = False
        self._settle_until = 0.0
        self._applied_ok = False
        self.last_error = ""
        self._cached_status: dict[str, Any] = {}
        self._cached_status = self._status_snapshot(success=None, probe=False)

    def go2(self) -> bool:
        return self.product in GO2_PRODUCTS or os.path.lexists(self.led_path)

    def rgb_path(self) -> str | None:
        path = self.led_path
        if not os.path.isdir(path):
            return None
        for attr in REQUIRED_ATTRS:
            attr_path = os.path.join(path, attr)
            if not os.path.exists(attr_path) or not os.access(attr_path, os.W_OK):
                return None
        return path

    def available_effects(self) -> list[dict[str, str]]:
        path = self.rgb_path()
        names = set(EFFECTS)
        if path:
            index_path = os.path.join(path, "effect_index")
            try:
                names = set(read_text(index_path).split()) & set(EFFECTS)
            except OSError:
                pass
        return [{"id": key, "label": EFFECTS[key]} for key in EFFECTS if key in names]

    def _channel_maxima(self, path: str) -> tuple[int, int, int]:
        try:
            order = read_text(os.path.join(path, "multi_index")).split()
            if order != ["red", "green", "blue"]:
                raise ValueError("unexpected RGB channel order")
            parts = [int(item) for item in read_text(os.path.join(path, "multi_max_intensity")).split()]
            if len(parts) >= 3 and all(0 < value <= 255 for value in parts[:3]):
                return parts[0], parts[1], parts[2]
        except (OSError, ValueError):
            pass
        return 100, 100, 100

    def _max_brightness(self, path: str) -> int:
        try:
            value = int(read_text(os.path.join(path, "max_brightness")))
            if 0 < value <= 255:
                return value
        except (OSError, ValueError):
            pass
        return 100

    def _write(self, path: str, attr: str, value: str) -> bool:
        target = os.path.join(path, attr)
        try:
            def _do_write() -> bool:
                write_text(target, value)
                return True

            ok = run_with_timeout(_do_write, default=False)
            if not ok:
                self.last_error = f"Timed out writing {attr}"
                self.log.warning("Timed out writing %s", attr)
                return False
            return True
        except OSError as exc:
            self.log.warning("Failed to write %s: %s", attr, exc)
            self.last_error = f"Could not write {attr}"
            return False

    def _read(self, path: str, attr: str) -> str | None:
        try:
            text = run_with_timeout(
                lambda: read_text(os.path.join(path, attr)), timeout=0.8, default=None
            )
            return text.lower() if isinstance(text, str) else None
        except OSError:
            return None

    def apply_rgb(self, *, force: bool = True) -> bool:
        path = self.rgb_path()
        if path is None:
            self.last_error = "Waiting for controllers"
            return False
        settings = self.settings
        if not settings["rings_enabled"]:
            return self._write(path, "enabled", "false")

        maxima = self._channel_maxima(path)
        brightness_max = self._max_brightness(path)
        brightness = round(brightness_max * settings["brightness"] / 100)
        color = hsv_to_sysfs(settings["hue"], settings["saturation"], maxima)
        writes = (
            ("profile", RGB_PROFILE),
            ("mode", RGB_MODE),
            ("speed", str(settings["speed"])),
            ("multi_intensity", color),
            ("brightness", str(brightness)),
            ("effect", settings["effect"]),
            ("enabled", "true"),
        )
        if not force:
            current_enabled = self._read(path, "enabled")
            if current_enabled == "true":
                current_effect = self._read(path, "effect")
                current_brightness = self._read(path, "brightness")
                if (
                    current_effect == settings["effect"]
                    and current_brightness == str(brightness)
                ):
                    color_now = self._read(path, "multi_intensity")
                    if color_now == color.lower():
                        return True
        for attr, value in writes:
            if not self._write(path, attr, value):
                return False
        self.last_error = ""
        return True

    def apply_power(self) -> bool:
        if not self.power_led.available():
            return False
        if self.suspended and self.settings["power_led_sleep_off"]:
            return self.power_led.set(False)
        return self.power_led.set(self.settings["power_led_enabled"])

    def apply(self, *, force: bool = True) -> bool:
        with self.lock:
            return self._apply_locked(force=force)

    def _apply_locked(self, *, force: bool = True) -> bool:
        rgb_ok = self.apply_rgb(force=force)
        power_ok = True
        if self.power_led.available():
            power_ok = self.apply_power()
        ok = rgb_ok and power_ok
        self._applied_ok = ok
        self._cached_status = self._status_snapshot(success=ok, probe=True)
        return ok

    def _rings_drifted(self) -> bool:
        path = self.rgb_path()
        if path is None:
            return True
        enabled = self._read(path, "enabled")
        if enabled is None:
            return True
        want_on = self.settings["rings_enabled"]
        if want_on and enabled != "true":
            return True
        if not want_on and enabled != "false":
            return True
        if not want_on:
            return False
        if self._read(path, "effect") != self.settings["effect"]:
            return True
        brightness_max = self._max_brightness(path)
        want = str(round(brightness_max * self.settings["brightness"] / 100))
        return self._read(path, "brightness") != want

    def persist(self) -> None:
        self.store.save(self.settings)

    def update(self, **changes: Any) -> dict[str, Any]:
        with self.lock:
            merged = dict(self.settings)
            merged.update(changes)
            self.settings = sanitize_settings(merged)
            self.persist()
            self._apply_locked(force=True)
            return dict(self._cached_status)

    def suspend(self) -> dict[str, Any]:
        with self.lock:
            self.suspended = True
            if self.settings["power_led_sleep_off"]:
                self.power_led.set(False)
            self._cached_status = self._status_snapshot(success=True, probe=False)
            return dict(self._cached_status)

    def resume(self) -> dict[str, Any]:
        with self.lock:
            self.suspended = False
            self._settle_until = time.monotonic() + self.settle_s
            self._applied_ok = False
            self.resume_watch.mark()
            self._apply_locked(force=True)
            return dict(self._cached_status)

    def _status_snapshot(self, success: bool | None, probe: bool) -> dict[str, Any]:
        rgb_ready = False
        power_current = None
        if probe:
            rgb_ready = self.rgb_path() is not None
            power_current = self.power_led.get()
        power_ready = power_current is not None
        cached = self._cached_status
        if not probe and cached:
            rgb_ready = bool(cached.get("rgb_ready"))
            power_ready = bool(cached.get("power_ready"))
            power_current = cached.get("power_current")
        if rgb_ready:
            rgb_reason = ""
        elif probe:
            rgb_reason = "Waiting for controllers"
        else:
            rgb_reason = "Starting"
        result: dict[str, Any] = {
            "go2": self.go2(),
            "product": self.product,
            "huesync": self.huesync,
            "rgb_ready": rgb_ready,
            "rgb_reason": rgb_reason,
            "power_ready": power_ready,
            "power_reason": "" if power_ready else "Power LED is unavailable",
            "power_current": power_current,
            "effects": [{"id": key, "label": EFFECTS[key]} for key in EFFECTS],
            "settings": dict(self.settings),
            "last_error": self.last_error,
            "suspended": self.suspended,
        }
        if success is not None:
            result["success"] = success
            if not success and self.last_error:
                result["error"] = self.last_error
        return result

    def status(self, success: bool | None = None) -> dict[str, Any]:
        with self.lock:
            result = dict(self._cached_status)
            result["settings"] = dict(self.settings)
            result["last_error"] = self.last_error
            result["suspended"] = self.suspended
            if success is not None:
                result["success"] = success
                if not success and self.last_error:
                    result["error"] = self.last_error
            return result

    def stop(self) -> None:
        self._running = False

    def _tick(self) -> None:
        if self.resume_watch():
            self.log.info("Resume from suspend; reapplying lights")
            self.resume()
            return
        now = time.monotonic()
        settling = now < self._settle_until
        ready = self.rgb_path() is not None
        if not ready or settling or not self._applied_ok or self._rings_drifted():
            self.apply(force=True)

    async def run(self) -> None:
        import asyncio

        self._running = True
        self.resume_watch.mark()
        self._settle_until = time.monotonic() + self.settle_s
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                await loop.run_in_executor(None, self._tick)
                now = time.monotonic()
                settling = now < self._settle_until
                ready = self._cached_status.get("rgb_ready")
                delay = 1.0 if settling or not ready else 15.0
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("Lighting loop failed")
                await asyncio.sleep(5.0)
