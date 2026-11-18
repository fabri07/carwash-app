import { redirect } from "next/navigation";

/** `/operacion` no tiene pantalla propia: la operación arranca en la agenda del día. */
export default function OperacionPage() {
  redirect("/agenda");
}
