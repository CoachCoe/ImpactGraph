import type { Money } from "@/lib/types";

/** Minor units are integers; divide only at the point of display. */
export function formatMoney(money: Money, options: Intl.NumberFormatOptions = {}): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: money.currency,
    ...options,
  }).format(money.amountMinor / 100);
}
