import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "py_modules"))

from lights import (
    EFFECTS,
    JsonStore,
    LightsController,
    MemoryPowerLed,
    hsv_to_sysfs,
    huesync_present,
    sanitize_settings,
)


def make_led(root: Path) -> Path:
    led = root / "go:rgb:joystick_rings"
    led.mkdir()
    (led / "enabled").write_text("false\n")
    (led / "enabled_index").write_text("true false\n")
    (led / "profile").write_text("0\n")
    (led / "profile_range").write_text("1-3\n")
    (led / "mode").write_text("dynamic\n")
    (led / "mode_index").write_text("dynamic custom\n")
    (led / "effect").write_text("monocolor\n")
    (led / "effect_index").write_text("monocolor breathe chroma rainbow\n")
    (led / "brightness").write_text("100\n")
    (led / "max_brightness").write_text("100\n")
    (led / "multi_intensity").write_text("0 0 0\n")
    (led / "multi_max_intensity").write_text("100 100 100\n")
    (led / "multi_index").write_text("red green blue\n")
    (led / "speed").write_text("50\n")
    (led / "speed_range").write_text("0-100\n")
    for name in (
        "enabled",
        "profile",
        "mode",
        "effect",
        "brightness",
        "multi_intensity",
        "speed",
    ):
        os.chmod(led / name, 0o644)
    return led


class LightsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.led = make_led(self.root)
        self.store_path = self.root / "settings.json"
        self.power = MemoryPowerLed(initial_on=True)
        self.lights = LightsController(
            store=JsonStore(str(self.store_path)),
            led_path=str(self.led),
            power_led=self.power,
            product="83N0",
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_solid_uses_full_value_color_and_slider_brightness(self):
        result = self.lights.update(
            effect="monocolor", hue=0, saturation=100, brightness=20, rings_enabled=True
        )
        self.assertTrue(result["success"])
        self.assertEqual((self.led / "multi_intensity").read_text().strip(), "100 0 0")
        self.assertEqual((self.led / "brightness").read_text().strip(), "20")
        self.assertEqual((self.led / "enabled").read_text().strip(), "true")
        self.assertEqual((self.led / "profile").read_text().strip(), "3")
        self.assertEqual((self.led / "mode").read_text().strip(), "custom")
        self.assertEqual((self.led / "effect").read_text().strip(), "monocolor")

    def test_hsv_red_scales_to_channel_maxima(self):
        self.assertEqual(hsv_to_sysfs(0, 100, (100, 100, 100)), "100 0 0")
        self.assertEqual(hsv_to_sysfs(0, 100, (255, 255, 255)), "255 0 0")

    def test_firmware_reset_enabled_is_rewritten(self):
        self.lights.update(rings_enabled=True, brightness=40)
        (self.led / "enabled").write_text("false\n")
        self.assertTrue(self.lights._rings_drifted())
        self.assertTrue(self.lights.apply(force=True))
        self.assertEqual((self.led / "enabled").read_text().strip(), "true")

    def test_missing_sysfs_waits_then_applies(self):
        missing = self.root / "missing-led"
        lights = LightsController(
            store=JsonStore(str(self.root / "other.json")),
            led_path=str(missing),
            power_led=self.power,
            product="83N0",
        )
        self.assertFalse(lights.apply(force=True))
        self.assertEqual(lights.status()["rgb_reason"], "Waiting for controllers")
        later = self.root / "later"
        later.mkdir()
        led = make_led(later)
        lights.led_path = str(led)
        self.assertTrue(lights.apply(force=True))
        self.assertTrue(lights.status()["rgb_ready"])

    def test_spiral_writes_rainbow_effect(self):
        self.lights.update(effect="rainbow", rings_enabled=True, speed=80)
        self.assertEqual((self.led / "effect").read_text().strip(), "rainbow")
        self.assertEqual((self.led / "speed").read_text().strip(), "80")
        self.assertEqual((self.led / "enabled").read_text().strip(), "true")

    def test_rings_off_only_writes_enabled_false(self):
        self.lights.update(rings_enabled=True)
        self.lights.update(rings_enabled=False)
        self.assertEqual((self.led / "enabled").read_text().strip(), "false")

    def test_power_led_inverted_bit(self):
        self.assertTrue(self.power.get())
        self.lights.update(power_led_enabled=False)
        self.assertFalse(self.power.get())
        self.lights.update(power_led_enabled=True)
        self.assertTrue(self.power.get())

    def test_suspend_turns_power_led_off_and_resume_restores(self):
        self.lights.update(power_led_enabled=True, power_led_sleep_off=True)
        self.assertTrue(self.power.get())
        self.lights.suspend()
        self.assertFalse(self.power.get())
        self.assertTrue(self.lights.suspended)
        # The background loop must not turn the LED back on while asleep.
        self.lights.apply_power()
        self.assertFalse(self.power.get())
        self.lights.resume()
        self.assertTrue(self.power.get())
        self.assertFalse(self.lights.suspended)
        self.assertEqual((self.led / "enabled").read_text().strip(), "true")

    def test_sleep_off_disabled_leaves_power_led_on(self):
        self.lights.update(power_led_enabled=True, power_led_sleep_off=False)
        self.lights.suspend()
        self.assertTrue(self.power.get())

    def test_settings_round_trip(self):
        self.lights.update(hue=210, saturation=80, brightness=35, effect="breathe")
        restored = LightsController(
            store=JsonStore(str(self.store_path)),
            led_path=str(self.led),
            power_led=MemoryPowerLed(),
            product="83N0",
        )
        self.assertEqual(restored.settings["hue"], 210)
        self.assertEqual(restored.settings["effect"], "breathe")
        self.assertEqual(restored.settings["brightness"], 35)

    def test_sanitize_rejects_unknown_effect(self):
        clean = sanitize_settings({"effect": "disco", "brightness": 500, "hue": -3})
        self.assertEqual(clean["effect"], "monocolor")
        self.assertEqual(clean["brightness"], 100)
        self.assertEqual(clean["hue"], 0)

    def test_huesync_detection(self):
        plugins = self.root / "plugins"
        ours = plugins / "LegionGo2Lights"
        theirs = plugins / "HueSync"
        ours.mkdir(parents=True)
        theirs.mkdir()
        (theirs / "plugin.json").write_text('{"name": "HueSync"}\n')
        self.assertTrue(huesync_present(str(ours)))
        self.assertFalse(huesync_present(str(self.root / "empty" / "x")))

    def test_status_returns_before_hardware_apply(self):
        status = self.lights.status()
        self.assertTrue(status["go2"])
        self.assertEqual(status["product"], "83N0")
        self.assertEqual(status["rgb_reason"], "Starting")
        self.assertIn("rings_enabled", status["settings"])

    def test_default_sleep_off_is_on(self):
        self.assertTrue(self.lights.settings["power_led_sleep_off"])

    def test_effect_labels_include_spiral(self):
        self.assertEqual(EFFECTS["rainbow"], "Spiral")
        self.assertEqual(EFFECTS["chroma"], "Color cycle")
        ids = {item["id"] for item in self.lights.available_effects()}
        self.assertEqual(ids, set(EFFECTS))


if __name__ == "__main__":
    unittest.main()
