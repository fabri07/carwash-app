import { Suspense } from "react";
import type { Metadata } from "next";

import { BookingArrivalClient } from "@/features/operacion/BookingArrivalClient";

export const metadata: Metadata = {
  title: "Llegada | carwash.app",
};

export default async function BookingArrivalPage({
  params,
}: {
  params: Promise<{ bookingId: string }>;
}) {
  const { bookingId } = await params;
  return (
    <Suspense fallback={null}>
      <BookingArrivalClient bookingId={bookingId} />
    </Suspense>
  );
}
