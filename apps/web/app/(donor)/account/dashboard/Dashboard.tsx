"use client";

import Link from "next/link";
import { useState } from "react";
import { Announcer } from "@/components/donor/Announcer";
import { Page } from "@/components/donor/Page";
import { SignInPrompt } from "@/components/donor/SignInPrompt";
import { TodayInitiative } from "@/components/donor/TodayInitiative";
import type { Schema } from "@/lib/api";
import { formatDateTime } from "@/lib/dates";
import { formatMoney } from "@/lib/money";
import { resourceMessages, useApiResource } from "@/lib/useApiResource";

const streakDayKeys = Array.from({ length: 30 }, (_, day) => `streak-day-${day + 1}`);

function LoadingMetric({
  title,
  children,
  detail = false,
}: {
  title: string;
  children: React.ReactNode;
  detail?: boolean;
}) {
  return (
    <article className="metric">
      <h2>{title}</h2>
      <p className="metricValue">
        <span className="metricSkeleton" />
      </p>
      <p>{children}</p>
      {detail && (
        <p className="metricSkeletonDetail">
          <span>View Link and IRL breakdown</span>
          <span>+</span>
        </p>
      )}
    </article>
  );
}

export default function Dashboard() {
  const { resource, data, failed, reload } = useApiResource<Schema<"DonorDashboard">>("/donor/me/dashboard");
  const [shownInitiatives, setShownInitiatives] = useState(0);
  const messages = resourceMessages(resource, {
    loading: "Loading your dashboard…",
    failure: "Your dashboard could not be loaded right now. Please retry in a moment.",
  });
  const updating =
    data?.state === "preparing"
      ? "We’re preparing your dashboard. Please refresh shortly."
      : data?.state === "ready" && !data.referrals
        ? "Referral totals are updating. Please refresh shortly."
        : data?.state === "ready" && !data.streak
          ? "Your streak is updating. Please refresh shortly."
          : "";

  return (
    <Page wide eyebrow="My account" title="Your impact">
      <p className="note">
        DAD launches in April 2027. This preview shows test payments and simulated recognition only.
      </p>
      {resource.state === "signedOut" ? (
        <SignInPrompt>Sign in to view your dashboard</SignInPrompt>
      ) : (
        <>
          {(resource.state === "loading" || data?.state === "preparing") && (
            <>
              <div className="metrics" aria-hidden="true">
                <LoadingMetric title="Your giving">Given since you joined, after any refunds.</LoadingMetric>
                <LoadingMetric title="Initiatives funded">Initiatives your daily gift has gone to.</LoadingMetric>
                <LoadingMetric title="Events volunteered">Events you checked in at.</LoadingMetric>
              </div>
              <div className="metrics pair" aria-hidden="true">
                <LoadingMetric title="Link Referrals">People who gave after opening your link.</LoadingMetric>
                <LoadingMetric title="IRL Referrals">People who gave after scanning your code.</LoadingMetric>
              </div>
              <div className="metrics" aria-hidden="true">
                <LoadingMetric title="Money raised because of you" detail>
                  Collected test gifts after successful refunds.
                </LoadingMetric>
                <LoadingMetric title="Giving streak">
                  Consecutive days your monthly gift has stayed current.
                </LoadingMetric>
                <LoadingMetric title="Recognition">
                  <span className="metricSkeletonCopy">
                    Your first gift completed before 1 January 2027 (New York time). The badge is permanent. Sandbox
                    recognition only.
                  </span>
                </LoadingMetric>
              </div>
            </>
          )}
          {data?.state === "ready" && (
            <>
              {data.streak?.at_risk && (
                <div className="callout warn">
                  <p>
                    Your streak is at risk: your latest monthly payment didn’t go through.
                    {data.streak.grace_ends_at && (
                      <>
                        {" "}
                        Update your card by{" "}
                        <time dateTime={data.streak.grace_ends_at}>{formatDateTime(data.streak.grace_ends_at)}</time>,
                        when the grace period ends, to keep your {data.streak.ladder?.days ?? 0}-day streak.
                      </>
                    )}
                  </p>
                  <Link className="button" href="/account/payment-method">
                    Update payment method
                  </Link>
                </div>
              )}
              <div className="metrics">
                <article className="metric">
                  <h2>Your giving</h2>
                  <p className="metricValue">
                    {data.impact.given_minor === null ? "Updating" : formatMoney(data.impact.given_minor)}
                  </p>
                  <p>Given since you joined, after any refunds.</p>
                </article>
                <article className="metric">
                  <h2>Initiatives funded</h2>
                  <p className="metricValue">{Math.max(data.impact.initiatives_funded, shownInitiatives)}</p>
                  <p>Initiatives your daily gift has gone to.</p>
                </article>
                <article className="metric">
                  <h2>Events volunteered</h2>
                  <p className="metricValue">{data.impact.events_volunteered}</p>
                  <p>Events you checked in at.</p>
                </article>
              </div>
              <TodayInitiative onShown={setShownInitiatives} />
              <div className="metrics pair">
                <article className="metric">
                  <h2>Link Referrals</h2>
                  <p className="metricValue">{data.referrals?.link ?? "Updating"}</p>
                  <p>People who gave after opening your link.</p>
                </article>
                <article className="metric">
                  <h2>IRL Referrals</h2>
                  <p className="metricValue">{data.referrals?.irl ?? "Updating"}</p>
                  <p>People who gave after scanning your code.</p>
                </article>
              </div>
              <div className="metrics">
                <article className="metric">
                  <h2>Money raised because of you</h2>
                  <p className="metricValue">{data.referrals ? formatMoney(data.referrals.total_minor) : "Updating"}</p>
                  <p>Collected test gifts after successful refunds.</p>
                  {data.referrals && (
                    <details>
                      <summary>View Link and IRL breakdown</summary>
                      <p>Link: {formatMoney(data.referrals.link_minor)}</p>
                      <p>IRL: {formatMoney(data.referrals.irl_minor)}</p>
                    </details>
                  )}
                </article>
                <article className="metric">
                  <h2>Giving streak</h2>
                  <p className="metricValue">
                    {data.streak?.ladder
                      ? `${data.streak.ladder.days} ${data.streak.ladder.days === 1 ? "day" : "days"}`
                      : data.streak
                        ? "Starting today"
                        : "Updating"}
                  </p>
                  {data.streak?.ladder ? (
                    <>
                      <p>
                        {data.streak.ladder.next_milestone && data.streak.ladder.days_to_next
                          ? `${data.streak.ladder.days_to_next} days to your ${data.streak.ladder.next_milestone}-day milestone.`
                          : "Your giving streak keeps growing every day."}
                      </p>
                      <div
                        className="streakBar"
                        role="img"
                        aria-label={`${data.streak.ladder.bar.filter(Boolean).length} of the last 30 days in your current streak`}
                      >
                        {streakDayKeys.map((key, index) => (
                          <span
                            key={key}
                            className={data.streak?.ladder?.bar[index] ? "active" : ""}
                            aria-hidden="true"
                          />
                        ))}
                      </div>
                      {data.rank && (
                        <p>
                          Top {data.rank.national_percentile}% nationally
                          {data.rank.city_rank && data.rank.city
                            ? ` · #${data.rank.city_rank} in ${data.rank.city}`
                            : ""}
                        </p>
                      )}
                    </>
                  ) : data.streak ? (
                    <p>Your day-based streak begins with your current monthly gift.</p>
                  ) : (
                    <p>We’re checking your billing history. Your streak is temporarily unavailable.</p>
                  )}
                </article>
                {data.network && (
                  <article className="metric">
                    <h2>Your network</h2>
                    <p className="metricValue">{formatMoney(data.network.annualized_minor)} a year</p>
                    <p>
                      {data.network.active_count} active {data.network.active_count === 1 ? "donor" : "donors"} you
                      brought in.
                    </p>
                  </article>
                )}
                {data.founding_donor && (
                  <article className="metric">
                    <h2>Founding Donor</h2>
                    <p className="metricValue">Earned</p>
                    <p>
                      Your first gift completed before 1 January 2027 (New York time). The badge is permanent. Sandbox
                      recognition only.
                    </p>
                  </article>
                )}
              </div>
              {!data.founding_donor && (
                <p className="note">
                  Founding Donor badge: not yet earned. A first gift completed before 1 January 2027 earns it
                  permanently.
                </p>
              )}
            </>
          )}
          <Announcer
            status={messages.status || updating}
            alert={messages.alert}
            statusShownElsewhere={resource.state === "loading"}
          />
          {(failed || updating) && (
            <button type="button" className="secondary" onClick={reload}>
              Refresh dashboard
            </button>
          )}
          <nav aria-label="Account" className="links">
            <Link href="/account/events">Events near you</Link>
            <Link href="/account/badges">Badges</Link>
            <Link href="/account/network">Your network</Link>
            <Link href="/account/share">Share and invite</Link>
            <Link href="/account/code">My referral code</Link>
            <Link href="/account/giving">My giving</Link>
            <Link href="/account/profile">Profile and notifications</Link>
            <Link href="/account/employer">Employer and team</Link>
          </nav>
        </>
      )}
    </Page>
  );
}
