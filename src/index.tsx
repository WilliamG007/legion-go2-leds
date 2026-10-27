import { callable } from "@decky/api";
import {
  ButtonItem,
  DropdownItem,
  DropdownOption,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  definePlugin,
  gamepadSliderClasses,
  staticClasses,
} from "@decky/ui";
import { FC, useCallback, useEffect, useRef, useState } from "react";

interface Settings {
  rings_enabled: boolean;
  effect: string;
  hue: number;
  saturation: number;
  brightness: number;
  speed: number;
  power_led_enabled: boolean;
  power_led_sleep_off: boolean;
}

interface EffectOption {
  id: string;
  label: string;
}

interface Status {
  success?: boolean;
  error?: string;
  go2: boolean;
  product: string;
  huesync: boolean;
  rgb_ready: boolean;
  rgb_reason: string;
  power_ready: boolean;
  power_reason: string;
  power_current: boolean | null;
  effects: EffectOption[];
  settings: Settings;
  last_error: string;
  suspended: boolean;
}

const getStatus = callable<[], Status>("get_status");
const setRingsEnabled = callable<[boolean], Status>("set_rings_enabled");
const setEffect = callable<[string], Status>("set_effect");
const setColor = callable<[number, number], Status>("set_color");
const setBrightness = callable<[number], Status>("set_brightness");
const setSpeed = callable<[number], Status>("set_speed");
const setPowerLed = callable<[boolean], Status>("set_power_led");
const setPowerLedSleepOff = callable<[boolean], Status>("set_power_led_sleep_off");
const backendSuspend = callable<[], Status>("suspend");
const backendResume = callable<[], Status>("resume");

const COLOR_EFFECTS = new Set(["monocolor", "breathe"]);
const SPEED_EFFECTS = new Set(["breathe", "chroma", "rainbow"]);

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), ms);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        window.clearTimeout(timer);
        reject(err);
      },
    );
  });
}

type SteamSub = { unregister: () => void } | null;

function registerSuspend(callback: () => void): SteamSub {
  const steam = (window as unknown as { SteamClient?: any }).SteamClient;
  if (steam?.System?.RegisterForOnSuspendRequest) {
    return steam.System.RegisterForOnSuspendRequest(callback);
  }
  if (steam?.User?.RegisterForPrepareForSystemSuspendProgress) {
    return steam.User.RegisterForPrepareForSystemSuspendProgress(() => callback());
  }
  return null;
}

function registerResume(callback: () => void): SteamSub {
  const steam = (window as unknown as { SteamClient?: any }).SteamClient;
  if (steam?.System?.RegisterForOnResumeFromSuspend) {
    return steam.System.RegisterForOnResumeFromSuspend(callback);
  }
  if (steam?.User?.RegisterForResumeSuspendedGamesProgress) {
    return steam.User.RegisterForResumeSuspendedGamesProgress(() => callback());
  }
  return null;
}

const Icon: FC = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M9 21h6v-1.5H9V21zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17h8v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7z" />
  </svg>
);

const hsvCss = (hue: number, saturation: number, value: number) => {
  const h = ((hue % 360) + 360) % 360;
  const s = Math.max(0, Math.min(100, saturation)) / 100;
  const v = Math.max(0, Math.min(100, value)) / 100;
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 60) [r, g, b] = [c, x, 0];
  else if (h < 120) [r, g, b] = [x, c, 0];
  else if (h < 180) [r, g, b] = [0, c, x];
  else if (h < 240) [r, g, b] = [0, x, c];
  else if (h < 300) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return `rgb(${Math.round((r + m) * 255)}, ${Math.round((g + m) * 255)}, ${Math.round((b + m) * 255)})`;
};

const Content: FC = () => {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const pending = useRef(0);
  const mounted = useRef(true);
  const timers = useRef<Partial<Record<string, ReturnType<typeof setTimeout>>>>({});

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const timer of Object.values(timers.current)) {
        if (timer) clearTimeout(timer);
      }
    };
  }, []);

  const refresh = useCallback(async () => {
    if (pending.current > 0) return;
    let lastError = "The plugin backend did not reply";
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        const next = await withTimeout(getStatus(), 8000, lastError);
        if (!mounted.current || pending.current > 0) return;
        setStatus(next);
        setError(next.last_error || next.error || "");
        return;
      } catch (err) {
        lastError = err instanceof Error ? err.message : "Could not read lighting status";
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
    }
    if (mounted.current) setError(lastError);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), status?.rgb_ready ? 8000 : 1500);
    return () => clearInterval(timer);
  }, [refresh, status?.rgb_ready]);

  const run = useCallback(async (operation: () => Promise<Status>) => {
    pending.current += 1;
    try {
      const next = await operation();
      if (!mounted.current) return;
      setStatus(next);
      setError(next.success === false ? next.error || next.last_error || "The lights did not confirm" : "");
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : "The change failed");
    } finally {
      pending.current -= 1;
    }
  }, []);

  const patch = (changes: Partial<Settings>) => {
    setStatus((current) =>
      current
        ? { ...current, settings: { ...current.settings, ...changes } }
        : current
    );
  };

  const schedule = (key: string, changes: Partial<Settings>, operation: () => Promise<Status>) => {
    patch(changes);
    const previous = timers.current[key];
    if (previous) clearTimeout(previous);
    timers.current[key] = setTimeout(() => {
      delete timers.current[key];
      void run(operation);
    }, 180);
  };

  if (!status) {
    return (
      <PanelSection title="Legion Go 2 LEDs">
        <PanelSectionRow>
          <div>{error || "Loading…"}</div>
        </PanelSectionRow>
        {error && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => void refresh()}>
              Retry
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
    );
  }

  if (!status.go2) {
    return (
      <PanelSection title="Legion Go 2 LEDs">
        <PanelSectionRow>
          <div>
            {status.last_error || status.error
              ? status.last_error || status.error
              : `This plugin is for the Legion Go 2 (83N0 / 83N1). This device reports ${status.product || "an unknown model"}.`}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void refresh()}>
            Retry
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const settings = status.settings;
  const effectOptions: DropdownOption[] = status.effects.map((effect) => ({
    data: effect.id,
    label: effect.label,
  }));
  const usesColor = COLOR_EFFECTS.has(settings.effect);
  const usesSpeed = SPEED_EFFECTS.has(settings.effect);
  const swatch = (
    <span
      aria-label="Stick light color"
      style={{
        display: "inline-block",
        width: "0.8em",
        height: "0.8em",
        borderRadius: "50%",
        marginRight: "6px",
        verticalAlign: "middle",
        background: hsvCss(settings.hue, settings.saturation, settings.brightness),
      }}
    />
  );

  return (
    <>
      {status.huesync && (
        <PanelSection title="Conflict">
          <PanelSectionRow>
            <div>HueSync is still installed. Uninstall it so the two plugins do not fight over the stick lights.</div>
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="Power button">
        {!status.power_ready && (
          <PanelSectionRow>
            <div>{status.power_reason}</div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ToggleField
            label="Power ON LED"
            checked={settings.power_led_enabled}
            disabled={!status.power_ready}
            onChange={(value: boolean) => {
              patch({ power_led_enabled: value });
              void run(() => setPowerLed(value));
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Sleep Mode LED"
            checked={!settings.power_led_sleep_off}
            disabled={!status.power_ready || !settings.power_led_enabled}
            onChange={(value: boolean) => {
              patch({ power_led_sleep_off: !value });
              void run(() => setPowerLedSleepOff(!value));
            }}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Joysticks">
        {!status.rgb_ready && (
          <PanelSectionRow>
            <div>{status.rgb_reason}. Settings are kept and applied when the controllers return.</div>
          </PanelSectionRow>
        )}
        {error && status.rgb_ready && (
          <PanelSectionRow>
            <div>{error}</div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ToggleField
            label="Joystick LEDs"
            checked={settings.rings_enabled}
            onChange={(value: boolean) => {
              patch({ rings_enabled: value });
              void run(() => setRingsEnabled(value));
            }}
          />
        </PanelSectionRow>
        {settings.rings_enabled && (
          <>
            <PanelSectionRow>
              <DropdownItem
                label="Effect"
                rgOptions={effectOptions}
                selectedOption={settings.effect}
                strDefaultLabel={status.effects.find((item) => item.id === settings.effect)?.label || "Solid"}
                onChange={(option: DropdownOption) => {
                  const effect = String(option.data);
                  patch({ effect });
                  void run(() => setEffect(effect));
                }}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                className="Go2LightsBrightness"
                label="Brightness"
                value={settings.brightness}
                min={0}
                max={100}
                step={1}
                showValue
                valueSuffix="%"
                onChange={(value: number) =>
                  schedule("brightness", { brightness: Math.round(value) }, () =>
                    setBrightness(Math.round(value))
                  )
                }
              />
            </PanelSectionRow>
            {usesColor && (
              <>
                <PanelSectionRow>
                  <SliderField
                    className="Go2LightsHue"
                    label="Hue"
                    description={<span>{swatch}Both sticks</span>}
                    value={settings.hue}
                    min={0}
                    max={359}
                    step={1}
                    showValue
                    valueSuffix="°"
                    onChange={(value: number) =>
                      schedule("color", { hue: Math.round(value) }, () =>
                        setColor(Math.round(value), settings.saturation)
                      )
                    }
                  />
                </PanelSectionRow>
                <PanelSectionRow>
                  <SliderField
                    className="Go2LightsSaturation"
                    label="Saturation"
                    value={settings.saturation}
                    min={0}
                    max={100}
                    step={1}
                    showValue
                    valueSuffix="%"
                    onChange={(value: number) =>
                      schedule("color", { saturation: Math.round(value) }, () =>
                        setColor(settings.hue, Math.round(value))
                      )
                    }
                  />
                </PanelSectionRow>
              </>
            )}
            {usesSpeed && (
              <PanelSectionRow>
                <SliderField
                  label="Speed"
                  value={settings.speed}
                  min={0}
                  max={100}
                  step={1}
                  showValue
                  onChange={(value: number) =>
                    schedule("speed", { speed: Math.round(value) }, () =>
                      setSpeed(Math.round(value))
                    )
                  }
                />
              </PanelSectionRow>
            )}
          </>
        )}
        <style>{`
          .Go2LightsHue .${gamepadSliderClasses.SliderTrack} {
            background: linear-gradient(to right,
              hsl(0,100%,50%), hsl(60,100%,50%), hsl(120,100%,50%),
              hsl(180,100%,50%), hsl(240,100%,50%), hsl(300,100%,50%),
              hsl(360,100%,50%)) !important;
            --left-track-color: transparent !important;
            --colored-toggles-main-color: transparent !important;
          }
          .Go2LightsSaturation .${gamepadSliderClasses.SliderTrack} {
            background: linear-gradient(to right, white, hsl(${settings.hue},100%,50%)) !important;
            --left-track-color: transparent !important;
            --colored-toggles-main-color: transparent !important;
          }
          .Go2LightsBrightness .${gamepadSliderClasses.SliderTrack} {
            background: linear-gradient(to right, black, hsl(${settings.hue},${usesColor ? settings.saturation : 0}%,50%)) !important;
            --left-track-color: transparent !important;
            --colored-toggles-main-color: transparent !important;
          }
        `}</style>
      </PanelSection>
    </>
  );
};

export default definePlugin(() => {
  const suspendSub = registerSuspend(() => {
    void backendSuspend();
  });
  const resumeSub = registerResume(() => {
    void backendResume();
  });
  return {
    title: <div className={staticClasses.Title}>Legion Go 2 LEDs</div>,
    content: <Content />,
    icon: <Icon />,
    onDismount() {
      suspendSub?.unregister();
      resumeSub?.unregister();
    },
  };
});
