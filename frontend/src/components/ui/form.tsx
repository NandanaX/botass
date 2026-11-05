"use client";

import * as LabelPrimitive from "@radix-ui/react-label";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as React from "react";
import { cn } from "@/lib/utils";

/** Set by Field: the control inside takes this id, which links it to the Field's visible label. */
const FieldIds = React.createContext<{ controlId: string; labelId: string } | null>(null);

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => {
    const field = React.useContext(FieldIds);
    return (
      <input
        ref={ref}
        id={field?.controlId}
        className={cn(
          "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm tabular placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
          className,
        )}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";

/** Native select styled like shadcn/ui inputs (accessible and keyboard friendly). */
export const NativeSelect = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => {
    const field = React.useContext(FieldIds);
    return (
      <select
        ref={ref}
        id={field?.controlId}
        className={cn(
          "flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
          className,
        )}
        {...props}
      >
        {children}
      </select>
    );
  },
);
NativeSelect.displayName = "NativeSelect";

export function Label({ className, ...props }: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return <LabelPrimitive.Root className={cn("text-xs font-medium text-muted-foreground", className)} {...props} />;
}

export function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  const field = React.useContext(FieldIds);
  return (
    <SwitchPrimitive.Root
      id={field?.controlId}
      aria-labelledby={props["aria-label"] ? undefined : field?.labelId}
      className={cn(
        "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-muted-foreground/60",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block h-4 w-4 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0" />
    </SwitchPrimitive.Root>
  );
}

export function Field({ label, hint, labelExtra, children, className }: {
  label: string;
  hint?: string;
  labelExtra?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  const base = React.useId();
  const ids = React.useMemo(() => ({ controlId: `${base}-control`, labelId: `${base}-label` }), [base]);
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex min-h-4 items-center gap-1.5">
        <Label id={ids.labelId} htmlFor={ids.controlId}>{label}</Label>
        {labelExtra}
      </div>
      <FieldIds.Provider value={ids}>{children}</FieldIds.Provider>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}
