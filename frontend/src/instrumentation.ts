import * as Sentry from "@sentry/nextjs";

import { scrubSentryEvent } from "@/lib/sentryScrub";

export function register() {
  Sentry.init({
    dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT,
    sendDefaultPii: false,
    tracesSampleRate: 0.1,
    beforeSend: scrubSentryEvent,
  });
}

export const onRequestError = Sentry.captureRequestError;
