"use client";

import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as React from "react";
import { cn } from "@/lib/utils";

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn("inline-flex h-8 items-center rounded-md bg-muted p-0.5 text-muted-foreground", className)}
      {...props}
    />
  );
}

/** Most tabs here switch what the page shows rather than revealing a TabsContent; only those that do set ``hasPanel``. */
export function TabsTrigger({ className, hasPanel, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger> & { hasPanel?: boolean }) {
  return (
    <TabsPrimitive.Trigger
      {...(hasPanel ? {} : { "aria-controls": undefined })}
      className={cn(
        "inline-flex items-center justify-center rounded px-2.5 py-1 text-xs font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=active]:bg-background data-[state=active]:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("mt-3 focus-visible:outline-none", className)} {...props} />;
}
