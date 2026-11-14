import { BookingFlow } from "@/features/turnero/BookingFlow";

export default async function TurneroPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <BookingFlow slug={slug} />;
}
