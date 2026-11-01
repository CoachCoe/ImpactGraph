import type { Metadata } from "next";
import { Page } from "@/components/donor/Page";

export const metadata: Metadata = { title: "Return to the app", robots: { index: false } };

// Hosted checkout opened from the DAD app returns here. The app watches for this address
// and takes over; a browser without the app sees only this notice. The page is
// display-only: it reads nothing from the address except whether the donor paid or backed
// out, and the gift itself is settled by Stripe's verified webhook, never by this page.
export default async function AppReturnPage({ searchParams }: PageProps<"/app/return">) {
  const { result } = await searchParams;
  const cancelled = result === "cancel";
  return (
    <Page eyebrow="DAD app" title={cancelled ? "Checkout cancelled" : "You can return to the app"}>
      {cancelled ? (
        <p>No gift was started and nothing was charged. Return to the DAD app to choose an amount again.</p>
      ) : (
        <p>
          Thank you. Return to the DAD app to see your monthly gift. It appears once Stripe confirms the payment, which
          can take a moment.
        </p>
      )}
      <p className="note">In this sandbox preview it is a test gift record, and no real donation is collected.</p>
    </Page>
  );
}
