"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, NativeSelect } from "@/components/ui/form";
import { allTimeZones, browserTz, LOCAL_TZ, setTzChoice, tzLabel, useTzChoice } from "@/lib/timezone";

/** The one display time zone. Applies at once in this browser; it isn't part of saved settings. */
export function DisplayTimeCard() {
  const choice = useTzChoice();
  return (
    <Card id="display-time">
      <CardHeader>
        <CardTitle>Display time zone</CardTitle>
        <CardDescription>Every time in this app uses this zone, with the other time on hover. It applies at once and is remembered in this browser. Stored data stays in UTC.</CardDescription>
      </CardHeader>
      <CardContent>
        <Field label="Time zone" className="max-w-sm">
          <NativeSelect value={choice} onChange={(e) => setTzChoice(e.target.value)}>
            <option value={LOCAL_TZ}>Browser local: {tzLabel(browserTz())}</option>
            {allTimeZones().map((z) => <option key={z} value={z}>{z}</option>)}
          </NativeSelect>
        </Field>
      </CardContent>
    </Card>
  );
}
