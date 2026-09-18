import * as Sentry from "@sentry/nextjs";

import { scrubSentryEvent } from "@/lib/sentryScrub";

// Sin DSN, `Sentry.init` no manda nada: el build y el dev local no necesitan credenciales.
Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT,
  sendDefaultPii: false,
  tracesSampleRate: 0.1,
  beforeSend: scrubSentryEvent,
});

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
