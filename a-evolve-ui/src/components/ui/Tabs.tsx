import * as TabsPrimitive from "@radix-ui/react-tabs";
import type { ReactNode } from "react";

export function Tabs({
  value,
  onValueChange,
  children
}: {
  value: string;
  onValueChange: (v: string) => void;
  children: ReactNode;
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={onValueChange}>
      {children}
    </TabsPrimitive.Root>
  );
}

export const TabsList = TabsPrimitive.List;
export const TabsContent = TabsPrimitive.Content;
export const TabsTrigger = TabsPrimitive.Trigger;
