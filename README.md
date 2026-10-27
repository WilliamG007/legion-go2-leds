# Legion Go 2 LEDs

A small [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for the Lenovo Legion Go 2 (`83N0` / `83N1`) **on SteamOS**.

**Platform:** SteamOS only (not Windows, Bazzite, or ChimeraOS).  
**Tested on:** SteamOS 3.9, Legion Go 2.

It only drives two lights:

- **Joystick ring RGB** through `hid-lenovo-go` (`/sys/class/leds/go:rgb:joystick_rings`)
- **Power-button LED** (LPBL at `0xFEEC2310` bit 6)

After boot and wake it waits for the controllers to reappear, then force-writes `enabled`, mode, color, and brightness. HueSync on this hardware often left the rings dark until you toggled Solid/Spiral; this plugin retries instead of giving up after one write.

Color is sent at full HSV value. The brightness slider is written only to the sysfs `brightness` file, so 20% is dim rather than almost off.

## Features

- **Power ON LED** and **Sleep Mode LED** (on/off; the power button cannot be analog-dimmed)
- **Joystick LEDs:** Solid, Breathing, Color cycle, Spiral
- Brightness, hue, saturation, and speed
- Re-applies lighting after suspend and when the HID rings come back late

Uninstall **HueSync** before using this. Both plugins write the same joystick LEDs.

## Install

1. Uninstall HueSync in Decky if it is present.
2. Download `LegionGo2LEDs.zip` from [Releases](https://github.com/PoppyS33d/legion-go2-leds/releases).
3. Copy the zip onto the Legion Go 2 internal disk, for example `/home/deck/` (Desktop Mode). Game Mode does not show USB sticks or FAT/exFAT SD cards in Decky’s file picker.
4. Game Mode → Quick Access → Decky → gear → **General** → enable Developer mode.
5. **Developer** → Install Plugin from ZIP → **Home** → the zip.
6. Reload Decky if the plugin does not appear.

## First check

- Joystick LEDs on, Solid, brightness 20%: rings dim but clearly visible
- Spiral: rotating rainbow around the rings
- Power ON LED on, Sleep Mode LED off, then sleep: power button is dark
- Wake: joystick and power LEDs come back without changing modes

## License

BSD-3-Clause
