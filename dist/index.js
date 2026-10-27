const manifest = {"name":"Legion Go 2 LEDs"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error('[@decky/api]: Failed to connect to the loader as as the loader API was not initialized. This is likely a bug in Decky Loader.');
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
}
catch {
    api = internalAPIConnection.connect(1, manifest.name);
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version 1. Some features may not work.`);
}
if (api._version != API_VERSION) {
    console.warn(`[@decky/api] Requested API version ${API_VERSION} but the running loader only supports version ${api._version}. Some features may not work.`);
}
const callable = api.callable;

const getStatus = callable("get_status");
const setRingsEnabled = callable("set_rings_enabled");
const setEffect = callable("set_effect");
const setColor = callable("set_color");
const setBrightness = callable("set_brightness");
const setSpeed = callable("set_speed");
const setPowerLed = callable("set_power_led");
const setPowerLedSleepOff = callable("set_power_led_sleep_off");
const backendSuspend = callable("suspend");
const backendResume = callable("resume");
const COLOR_EFFECTS = new Set(["monocolor", "breathe"]);
const SPEED_EFFECTS = new Set(["breathe", "chroma", "rainbow"]);
function withTimeout(promise, ms, message) {
    return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => reject(new Error(message)), ms);
        promise.then((value) => {
            window.clearTimeout(timer);
            resolve(value);
        }, (err) => {
            window.clearTimeout(timer);
            reject(err);
        });
    });
}
function registerSuspend(callback) {
    const steam = window.SteamClient;
    if (steam?.System?.RegisterForOnSuspendRequest) {
        return steam.System.RegisterForOnSuspendRequest(callback);
    }
    if (steam?.User?.RegisterForPrepareForSystemSuspendProgress) {
        return steam.User.RegisterForPrepareForSystemSuspendProgress(() => callback());
    }
    return null;
}
function registerResume(callback) {
    const steam = window.SteamClient;
    if (steam?.System?.RegisterForOnResumeFromSuspend) {
        return steam.System.RegisterForOnResumeFromSuspend(callback);
    }
    if (steam?.User?.RegisterForResumeSuspendedGamesProgress) {
        return steam.User.RegisterForResumeSuspendedGamesProgress(() => callback());
    }
    return null;
}
const Icon = () => (window.SP_REACT.createElement("svg", { width: "20", height: "20", viewBox: "0 0 24 24", fill: "currentColor", "aria-hidden": "true" },
    window.SP_REACT.createElement("path", { d: "M9 21h6v-1.5H9V21zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17h8v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7z" })));
const hsvCss = (hue, saturation, value) => {
    const h = ((hue % 360) + 360) % 360;
    const s = Math.max(0, Math.min(100, saturation)) / 100;
    const v = Math.max(0, Math.min(100, value)) / 100;
    const c = v * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = v - c;
    let r = 0;
    let g = 0;
    let b = 0;
    if (h < 60)
        [r, g, b] = [c, x, 0];
    else if (h < 120)
        [r, g, b] = [x, c, 0];
    else if (h < 180)
        [r, g, b] = [0, c, x];
    else if (h < 240)
        [r, g, b] = [0, x, c];
    else if (h < 300)
        [r, g, b] = [x, 0, c];
    else
        [r, g, b] = [c, 0, x];
    return `rgb(${Math.round((r + m) * 255)}, ${Math.round((g + m) * 255)}, ${Math.round((b + m) * 255)})`;
};
const Content = () => {
    const [status, setStatus] = SP_REACT.useState(null);
    const [error, setError] = SP_REACT.useState("");
    const pending = SP_REACT.useRef(0);
    const mounted = SP_REACT.useRef(true);
    const timers = SP_REACT.useRef({});
    SP_REACT.useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            for (const timer of Object.values(timers.current)) {
                if (timer)
                    clearTimeout(timer);
            }
        };
    }, []);
    const refresh = SP_REACT.useCallback(async () => {
        if (pending.current > 0)
            return;
        let lastError = "The plugin backend did not reply";
        for (let attempt = 0; attempt < 5; attempt += 1) {
            try {
                const next = await withTimeout(getStatus(), 8000, lastError);
                if (!mounted.current || pending.current > 0)
                    return;
                setStatus(next);
                setError(next.last_error || next.error || "");
                return;
            }
            catch (err) {
                lastError = err instanceof Error ? err.message : "Could not read lighting status";
                await new Promise((resolve) => window.setTimeout(resolve, 1000));
            }
        }
        if (mounted.current)
            setError(lastError);
    }, []);
    SP_REACT.useEffect(() => {
        void refresh();
        const timer = setInterval(() => void refresh(), status?.rgb_ready ? 8000 : 1500);
        return () => clearInterval(timer);
    }, [refresh, status?.rgb_ready]);
    const run = SP_REACT.useCallback(async (operation) => {
        pending.current += 1;
        try {
            const next = await operation();
            if (!mounted.current)
                return;
            setStatus(next);
            setError(next.success === false ? next.error || next.last_error || "The lights did not confirm" : "");
        }
        catch (err) {
            if (mounted.current)
                setError(err instanceof Error ? err.message : "The change failed");
        }
        finally {
            pending.current -= 1;
        }
    }, []);
    const patch = (changes) => {
        setStatus((current) => current
            ? { ...current, settings: { ...current.settings, ...changes } }
            : current);
    };
    const schedule = (key, changes, operation) => {
        patch(changes);
        const previous = timers.current[key];
        if (previous)
            clearTimeout(previous);
        timers.current[key] = setTimeout(() => {
            delete timers.current[key];
            void run(operation);
        }, 180);
    };
    if (!status) {
        return (window.SP_REACT.createElement(DFL.PanelSection, { title: "Legion Go 2 LEDs" },
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, error || "Loading…")),
            error && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement(DFL.ButtonItem, { layout: "below", onClick: () => void refresh() }, "Retry")))));
    }
    if (!status.go2) {
        return (window.SP_REACT.createElement(DFL.PanelSection, { title: "Legion Go 2 LEDs" },
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, status.last_error || status.error
                    ? status.last_error || status.error
                    : `This plugin is for the Legion Go 2 (83N0 / 83N1). This device reports ${status.product || "an unknown model"}.`)),
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement(DFL.ButtonItem, { layout: "below", onClick: () => void refresh() }, "Retry"))));
    }
    const settings = status.settings;
    const effectOptions = status.effects.map((effect) => ({
        data: effect.id,
        label: effect.label,
    }));
    const usesColor = COLOR_EFFECTS.has(settings.effect);
    const usesSpeed = SPEED_EFFECTS.has(settings.effect);
    const swatch = (window.SP_REACT.createElement("span", { "aria-label": "Stick light color", style: {
            display: "inline-block",
            width: "0.8em",
            height: "0.8em",
            borderRadius: "50%",
            marginRight: "6px",
            verticalAlign: "middle",
            background: hsvCss(settings.hue, settings.saturation, settings.brightness),
        } }));
    return (window.SP_REACT.createElement(window.SP_REACT.Fragment, null,
        status.huesync && (window.SP_REACT.createElement(DFL.PanelSection, { title: "Conflict" },
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, "HueSync is still installed. Uninstall it so the two plugins do not fight over the stick lights.")))),
        window.SP_REACT.createElement(DFL.PanelSection, { title: "Power button" },
            !status.power_ready && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, status.power_reason))),
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement(DFL.ToggleField, { label: "Power ON LED", checked: settings.power_led_enabled, disabled: !status.power_ready, onChange: (value) => {
                        patch({ power_led_enabled: value });
                        void run(() => setPowerLed(value));
                    } })),
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement(DFL.ToggleField, { label: "Sleep Mode LED", checked: !settings.power_led_sleep_off, disabled: !status.power_ready || !settings.power_led_enabled, onChange: (value) => {
                        patch({ power_led_sleep_off: !value });
                        void run(() => setPowerLedSleepOff(!value));
                    } }))),
        window.SP_REACT.createElement(DFL.PanelSection, { title: "Joysticks" },
            !status.rgb_ready && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null,
                    status.rgb_reason,
                    ". Settings are kept and applied when the controllers return."))),
            error && status.rgb_ready && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement("div", null, error))),
            window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                window.SP_REACT.createElement(DFL.ToggleField, { label: "Joystick LEDs", checked: settings.rings_enabled, onChange: (value) => {
                        patch({ rings_enabled: value });
                        void run(() => setRingsEnabled(value));
                    } })),
            settings.rings_enabled && (window.SP_REACT.createElement(window.SP_REACT.Fragment, null,
                window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                    window.SP_REACT.createElement(DFL.DropdownItem, { label: "Effect", rgOptions: effectOptions, selectedOption: settings.effect, strDefaultLabel: status.effects.find((item) => item.id === settings.effect)?.label || "Solid", onChange: (option) => {
                            const effect = String(option.data);
                            patch({ effect });
                            void run(() => setEffect(effect));
                        } })),
                window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                    window.SP_REACT.createElement(DFL.SliderField, { className: "Go2LightsBrightness", label: "Brightness", value: settings.brightness, min: 0, max: 100, step: 1, showValue: true, valueSuffix: "%", onChange: (value) => schedule("brightness", { brightness: Math.round(value) }, () => setBrightness(Math.round(value))) })),
                usesColor && (window.SP_REACT.createElement(window.SP_REACT.Fragment, null,
                    window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                        window.SP_REACT.createElement(DFL.SliderField, { className: "Go2LightsHue", label: "Hue", description: window.SP_REACT.createElement("span", null,
                                swatch,
                                "Both sticks"), value: settings.hue, min: 0, max: 359, step: 1, showValue: true, valueSuffix: "\u00B0", onChange: (value) => schedule("color", { hue: Math.round(value) }, () => setColor(Math.round(value), settings.saturation)) })),
                    window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                        window.SP_REACT.createElement(DFL.SliderField, { className: "Go2LightsSaturation", label: "Saturation", value: settings.saturation, min: 0, max: 100, step: 1, showValue: true, valueSuffix: "%", onChange: (value) => schedule("color", { saturation: Math.round(value) }, () => setColor(settings.hue, Math.round(value))) })))),
                usesSpeed && (window.SP_REACT.createElement(DFL.PanelSectionRow, null,
                    window.SP_REACT.createElement(DFL.SliderField, { label: "Speed", value: settings.speed, min: 0, max: 100, step: 1, showValue: true, onChange: (value) => schedule("speed", { speed: Math.round(value) }, () => setSpeed(Math.round(value))) }))))),
            window.SP_REACT.createElement("style", null, `
          .Go2LightsHue .${DFL.gamepadSliderClasses.SliderTrack} {
            background: linear-gradient(to right,
              hsl(0,100%,50%), hsl(60,100%,50%), hsl(120,100%,50%),
              hsl(180,100%,50%), hsl(240,100%,50%), hsl(300,100%,50%),
              hsl(360,100%,50%)) !important;
            --left-track-color: transparent !important;
            --colored-toggles-main-color: transparent !important;
          }
          .Go2LightsSaturation .${DFL.gamepadSliderClasses.SliderTrack} {
            background: linear-gradient(to right, white, hsl(${settings.hue},100%,50%)) !important;
            --left-track-color: transparent !important;
            --colored-toggles-main-color: transparent !important;
          }
          .Go2LightsBrightness .${DFL.gamepadSliderClasses.SliderTrack} {
            background: linear-gradient(to right, black, hsl(${settings.hue},${usesColor ? settings.saturation : 0}%,50%)) !important;
            --left-track-color: transparent !important;
            --colored-toggles-main-color: transparent !important;
          }
        `))));
};
var index = DFL.definePlugin(() => {
    const suspendSub = registerSuspend(() => {
        void backendSuspend();
    });
    const resumeSub = registerResume(() => {
        void backendResume();
    });
    return {
        title: window.SP_REACT.createElement("div", { className: DFL.staticClasses.Title }, "Legion Go 2 LEDs"),
        content: window.SP_REACT.createElement(Content, null),
        icon: window.SP_REACT.createElement(Icon, null),
        onDismount() {
            suspendSub?.unregister();
            resumeSub?.unregister();
        },
    };
});

export { index as default };
//# sourceMappingURL=index.js.map
