import asyncio
import os
import sys
import traceback

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (PLUGIN_DIR, os.path.join(PLUGIN_DIR, "py_modules")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import decky

_IMPORT_ERROR = ""
try:
    from lights import JsonStore, LightsController, huesync_present
except Exception as exc:
    JsonStore = None  # type: ignore
    LightsController = None  # type: ignore
    huesync_present = None  # type: ignore
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    try:
        decky.logger.error("Failed to import lights backend:\n%s", traceback.format_exc())
    except Exception:
        pass


def _error_status(message: str) -> dict:
    return {
        "go2": False,
        "product": "",
        "huesync": False,
        "rgb_ready": False,
        "rgb_reason": message,
        "power_ready": False,
        "power_reason": message,
        "power_current": None,
        "effects": [],
        "settings": {
            "rings_enabled": True,
            "effect": "monocolor",
            "hue": 0,
            "saturation": 100,
            "brightness": 40,
            "speed": 66,
            "power_led_enabled": True,
            "power_led_sleep_off": True,
        },
        "last_error": message,
        "suspended": False,
        "success": False,
        "error": message,
    }


class Plugin:
    def _controller(self):
        if _IMPORT_ERROR or LightsController is None or JsonStore is None:
            raise RuntimeError(_IMPORT_ERROR or "Lighting backend failed to import")
        lights = getattr(self, "lights", None)
        if lights is not None:
            return lights
        settings_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
        plugin_dir = getattr(decky, "DECKY_PLUGIN_DIR", PLUGIN_DIR)
        self.lights = LightsController(
            store=JsonStore(settings_path),
            huesync=bool(huesync_present(plugin_dir)),
            log=decky.logger,
        )
        return self.lights

    async def _offload(self, func):
        return await asyncio.get_running_loop().run_in_executor(None, func)

    async def _main(self):
        if _IMPORT_ERROR:
            decky.logger.error("Backend import failed: %s", _IMPORT_ERROR)
            return
        try:
            lights = self._controller()
            decky.logger.info(
                "Legion Go 2 LEDs started (product=%s go2=%s huesync=%s)",
                lights.product,
                lights.go2(),
                lights.huesync,
            )
            self._task = asyncio.create_task(lights.run())
        except Exception:
            decky.logger.error("Failed to start lighting backend:\n%s", traceback.format_exc())

    async def _unload(self):
        if hasattr(self, "lights"):
            self.lights.stop()
        task = getattr(self, "_task", None)
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        decky.logger.info("Legion Go 2 LEDs unloaded")

    async def get_status(self):
        if _IMPORT_ERROR:
            return _error_status(_IMPORT_ERROR)
        try:
            return self._controller().status()
        except Exception as exc:
            decky.logger.error("get_status failed:\n%s", traceback.format_exc())
            return _error_status(f"{type(exc).__name__}: {exc}")

    async def set_rings_enabled(self, enabled: bool):
        return await self._offload(
            lambda: self._controller().update(rings_enabled=bool(enabled))
        )

    async def set_effect(self, effect: str):
        return await self._offload(lambda: self._controller().update(effect=str(effect)))

    async def set_color(self, hue: int, saturation: int):
        return await self._offload(
            lambda: self._controller().update(hue=int(hue), saturation=int(saturation))
        )

    async def set_brightness(self, brightness: int):
        return await self._offload(
            lambda: self._controller().update(brightness=int(brightness))
        )

    async def set_speed(self, speed: int):
        return await self._offload(lambda: self._controller().update(speed=int(speed)))

    async def set_power_led(self, enabled: bool):
        return await self._offload(
            lambda: self._controller().update(power_led_enabled=bool(enabled))
        )

    async def set_power_led_sleep_off(self, enabled: bool):
        return await self._offload(
            lambda: self._controller().update(power_led_sleep_off=bool(enabled))
        )

    async def suspend(self):
        return await self._offload(self._controller().suspend)

    async def resume(self):
        return await self._offload(self._controller().resume)

    async def reapply(self):
        def _run():
            lights = self._controller()
            ok = lights.apply(force=True)
            return lights.status(success=ok)

        return await self._offload(_run)
