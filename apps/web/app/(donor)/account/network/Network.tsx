"use client";

import Link from "next/link";
import { Announcer } from "@/components/donor/Announcer";
import { Page } from "@/components/donor/Page";
import { SignInPrompt } from "@/components/donor/SignInPrompt";
import type { Schema } from "@/lib/api";
import { formatDollars } from "@/lib/money";
import { resourceMessages, useApiResource } from "@/lib/useApiResource";

/** The donor's referral network (N-1 to N-3). */
export default function Network() {
  const { resource, data, reload, failed } = useApiResource<Schema<"NetworkSummary">>("/donor/me/network");
  const messages = resourceMessages(resource, {
    loading: "Loading your network…",
    failure: "Your network could not be loaded right now. Please retry in a moment.",
  });
  return (
    <Page eyebrow="My account" title="Your network">
      {resource.state === "signedOut" ? (
        <SignInPrompt>Sign in to see your network</SignInPrompt>
      ) : (
        <>
          {data && (
            <>
              <p className="lead">
                {data.active_count === 0
                  ? "No one you invited is giving yet. Share your link to start your network."
                  : `Together your network funds ${formatDollars(data.annualized_minor)} a year.`}
              </p>
              <p>
                {data.active_count} active {data.active_count === 1 ? "donor" : "donors"} you brought in.
              </p>
              {data.next_milestone && (
                <p>
                  {data.next_milestone.remaining} more to reach {data.next_milestone.label} (
                  {data.next_milestone.target} active).
                </p>
              )}
              {data.invitees.length > 0 && (
                <ul>
                  {data.invitees.map((person, index) => (
                    // Names are not unique, and the list carries no identifiers by design.
                    // biome-ignore lint/suspicious/noArrayIndexKey: the list is replaced whole on each load
                    <li key={index}>
                      {person.name} · {person.active ? "Giving" : "Not giving"}
                    </li>
                  ))}
                </ul>
              )}
              {data.anonymous_active > 0 && (
                <p className="note">
                  {data.anonymous_active} more {data.anonymous_active === 1 ? "person is" : "people are"} giving but
                  chose not to be named.
                </p>
              )}
            </>
          )}
          <Announcer status={messages.status} alert={messages.alert} />
          {failed && (
            <button type="button" className="secondary" onClick={reload}>
              Retry loading
            </button>
          )}
          <p className="links">
            <Link href="/account/share">Share and invite</Link>
            <Link href="/account/dashboard">Back to your dashboard</Link>
          </p>
        </>
      )}
    </Page>
  );
}
