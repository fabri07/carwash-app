import type { Metadata } from "next";

import { MyBusinessPageClient } from "@/features/configuracion/MyBusinessPageClient";

export const metadata: Metadata = {
  title: "Mi negocio | carwash.app",
};

export default function MyBusinessPage() {
  return <MyBusinessPageClient />;
}
