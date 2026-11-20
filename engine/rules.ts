// Time-of-day rules. Ian: "don't play Slayer in the morning, play it in the afternoon."
// Hours are the listener's local hours, worked out on the server from the account's
// timezone setting (default Australia/Perth), never from the server clock.

export const DEFAULT_TZ = 'Australia/Perth';

export type Rule = {
  id: string;
  label: string;
  from: number; // local hour, inclusive
  to: number; // local hour, exclusive (wraps past midnight when to < from)
  heavy: number; // weight added per unit of heaviness (0..1)
  energy: number; // weight added per unit of energy above 0.5
  arc: number; // target energy for the set at this time
};

// The rules table. Small on purpose: edit here, the test pins the shape.
export const RULES: Rule[] = [
  { id: 'morning', label: 'Morning', from: 5, to: 11, heavy: -0.6, energy: -0.1, arc: 0.4 },
  { id: 'midday', label: 'Midday', from: 11, to: 13, heavy: 0, energy: 0.05, arc: 0.55 },
  { id: 'afternoon', label: 'Afternoon', from: 13, to: 18, heavy: 0.35, energy: 0.15, arc: 0.65 },
  { id: 'evening', label: 'Evening', from: 18, to: 22, heavy: 0.1, energy: 0, arc: 0.55 },
  { id: 'late', label: 'Late', from: 22, to: 5, heavy: -0.3, energy: -0.2, arc: 0.3 },
];

export function isValidTz(tz: string): boolean {
  try {
    new Intl.DateTimeFormat('en-AU', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export function localHour(now: Date, tz: string = DEFAULT_TZ): number {
  const zone = isValidTz(tz) ? tz : DEFAULT_TZ;
  const h = new Intl.DateTimeFormat('en-GB', { timeZone: zone, hour: '2-digit', hourCycle: 'h23' }).format(now);
  return Number(h) % 24;
}

export function localClock(now: Date, tz: string = DEFAULT_TZ): string {
  const zone = isValidTz(tz) ? tz : DEFAULT_TZ;
  return new Intl.DateTimeFormat('en-GB', { timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(now);
}

export function ruleFor(hour: number): Rule {
  for (const r of RULES) {
    const inside = r.from < r.to ? hour >= r.from && hour < r.to : hour >= r.from || hour < r.to;
    if (inside) return r;
  }
  return RULES[0];
}

export function ruleAt(now: Date, tz: string = DEFAULT_TZ): Rule {
  return ruleFor(localHour(now, tz));
}
