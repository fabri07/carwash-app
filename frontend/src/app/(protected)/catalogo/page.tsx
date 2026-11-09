import type { Metadata } from "next";

import { CatalogPageClient } from "@/features/catalogo/CatalogPageClient";

export const metadata: Metadata = {
  title: "Catálogo | carwash.app",
};

export default function CatalogPage() {
  return <CatalogPageClient />;
}
