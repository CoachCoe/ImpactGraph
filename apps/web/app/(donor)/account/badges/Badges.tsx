"use client";

import Image from "next/image";
import Link from "next/link";
import { Announcer } from "@/components/donor/Announcer";
import { Page } from "@/components/donor/Page";
import { SignInPrompt } from "@/components/donor/SignInPrompt";
import type { Schema } from "@/lib/api";
import { formatDate } from "@/lib/dates";
import { resourceMessages, useApiResource } from "@/lib/useApiResource";

/** The badge wall (B-1): earned badges with their date and perk, unearned ones with how to earn them. */
export default function Badges() {
  const { resource, data, reload, failed } = useApiResource<Schema<"BadgeList">>("/donor/me/badges");
  const messages = resourceMessages(resource, {
    loading: "Loading your badges…",
    failure: "Your badges could not be loaded right now. Please retry in a moment.",
  });
  const earned = data?.badges.filter((badge) => badge.earned) ?? [];
  const unearned = data?.badges.filter((badge) => !badge.earned) ?? [];
  return (
    <Page eyebrow="My account" title="Badges">
      {resource.state === "signedOut" ? (
        <SignInPrompt>Sign in to see your badges</SignInPrompt>
      ) : (
        <>
          {data && (
            <>
              <section aria-labelledby="earned">
                <h2 id="earned">Earned</h2>
                {earned.length ? (
                  <ul className="metrics">
                    {earned.map((badge) => (
                      <li key={badge.code} className="metric">
                        {badge.artwork && <Image unoptimized src={badge.artwork} alt="" width={64} height={64} />}
                        <h3>{badge.display_title}</h3>
                        {badge.earned_at && <p>Earned {formatDate(badge.earned_at)}</p>}
                        {badge.perk && <p>{badge.perk}</p>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>No badges yet. Your first one is closer than you think.</p>
                )}
              </section>
              {unearned.length > 0 && (
                <section aria-labelledby="unearned">
                  <h2 id="unearned">Still to earn</h2>
                  <ul className="metrics">
                    {unearned.map((badge) => (
                      <li key={badge.code} className="metric">
                        {badge.artwork && <Image unoptimized src={badge.artwork} alt="" width={64} height={64} />}
                        <h3>{badge.title}</h3>
                        <p>{badge.rule}</p>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
          <Announcer status={messages.status} alert={messages.alert} />
          {failed && (
            <button type="button" className="secondary" onClick={reload}>
              Retry loading
            </button>
          )}
          <p>
            <Link href="/account/dashboard">Back to your dashboard</Link>
          </p>
        </>
      )}
    </Page>
  );
}
