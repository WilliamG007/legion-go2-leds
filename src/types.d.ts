import type { ReactNode } from "react";

declare global {
  namespace JSX {
    interface IntrinsicElements {
      [elemName: string]: any;
    }
  }
}

declare module "@decky/ui" {
  export const definePlugin: (factory: () => {
    title: ReactNode;
    content: ReactNode;
    icon?: ReactNode;
    onDismount?: () => void;
  }) => any;
  export const PanelSection: any;
  export const PanelSectionRow: any;
  export const ToggleField: any;
  export const SliderField: any;
  export const DropdownItem: any;
  export const ButtonItem: any;
  export const staticClasses: { Title: string };
  export const gamepadSliderClasses: { SliderTrack: string };
  export type DropdownOption = { data: unknown; label: string };
}

declare module "@decky/api" {
  export function callable<Args extends any[], Ret>(name: string): (...args: Args) => Promise<Ret>;
}
