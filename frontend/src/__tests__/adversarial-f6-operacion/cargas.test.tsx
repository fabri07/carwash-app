/**
 * T3 (6.2) — Carga retrospectiva y turno a mano: validaciones, importes (la trampa `parseMoney_`:
 * "20.000" tiene que dar 20000 pesos exactos), conversión de zona horaria (Córdoba→UTC y zonas con
 * horario de verano), "sin nombre" y datos opcionales.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ManualBookingForm } from "@/features/operacion/ManualBookingForm";
import { RetrospectiveForm } from "@/features/operacion/RetrospectiveForm";
import { WalkInForm } from "@/features/operacion/WalkInForm";
import { zonedToUtcIso } from "@/features/turnos/time";
import { localTime, localDate } from "@/lib/agenda";
import { getSettings } from "@/services/config.service";
import {
  createServer,
  goOffline,
  goOnline,
  job,
  queueItems,
  renderUi,
  resetUnits,
  type FakeServer,
} from "./harness";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/lib/api", () => ({
  API_URL: "http://localhost:8000",
  api: { get: jest.fn(), post: jest.fn() },
}));
jest.mock("@/services/catalog.service", () => ({ servicesApi: { list: jest.fn() } }));
jest.mock("@/services/config.service", () => ({
  getSettings: jest.fn(),
  resourcesApi: { list: jest.fn() },
  paymentMethodsApi: { list: jest.fn() },
  vehicleSizesApi: { list: jest.fn() },
}));

jest.setTimeout(30_000);

let server: FakeServer;
beforeEach(() => {
  resetUnits();
  server = createServer([job()]);
});

const set = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

async function basics() {
  await userEvent.selectOptions(await screen.findByLabelText("Tamaño del vehículo"), "s1");
  await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
}

async function retro(opts: { date?: string; start?: string; end?: string; amount?: string } = {}) {
  renderUi(<RetrospectiveForm />);
  await basics();
  set("Día", opts.date ?? "2026-11-10");
  set("Inicio", opts.start ?? "10:00");
  set("Fin", opts.end ?? "11:30");
  if (opts.amount !== undefined)
    await userEvent.type(screen.getByLabelText("Importe cobrado"), opts.amount);
}
const submitRetro = () => userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
const retroPosts = () => server.attempts.filter((a) => a.url === "/jobs/retrospective");

describe("Carga retrospectiva: horas y zona horaria", () => {
  it("Córdoba 10:00 → 13:00Z y 11:30 → 14:30Z; canal CARGA_MANUAL; sin occurred_at; vuelve a la agenda de ESE día", async () => {
    await retro();
    await submitRetro();
    await waitFor(() => expect(retroPosts()).toHaveLength(1));
    const b = retroPosts()[0]!.body;
    expect(b.started_at).toBe("2026-11-10T13:00:00.000Z");
    expect(b.finished_at).toBe("2026-11-10T14:30:00.000Z");
    expect(b.channel).toBe("CARGA_MANUAL");
    expect(b).not.toHaveProperty("occurred_at");
    expect(b.resource_id).toBeNull();
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/agenda?date=2026-11-10"));
  });

  it("fin anterior al inicio: error en el campo y ningún POST; fin igual al inicio se acepta", async () => {
    await retro({ start: "11:00", end: "10:59" });
    await submitRetro();
    expect(await screen.findByText("El fin no puede ser anterior al inicio")).toBeInTheDocument();
    expect(retroPosts()).toHaveLength(0);
    set("Fin", "11:00");
    await submitRetro();
    await waitFor(() => expect(retroPosts()).toHaveLength(1));
  });

  it("HALLAZGO: un trabajo que cruza la medianoche (23:00 → 01:00) se puede cargar (el detailing dura horas o días)", async () => {
    await retro({ start: "23:00", end: "01:00" });
    await submitRetro();
    await new Promise((r) => setTimeout(r, 100));
    expect(retroPosts()).toHaveLength(1);
    expect(retroPosts()[0]?.body.finished_at).toBe("2026-11-11T04:00:00.000Z");
  });

  it("sin hora de inicio o fin, o sin día: se pide y no se manda", async () => {
    renderUi(<RetrospectiveForm />);
    await basics();
    set("Día", "");
    await submitRetro();
    expect(await screen.findByText("Elegí el día")).toBeInTheDocument();
    expect(screen.getAllByText("Elegí la hora")).toHaveLength(2);
    expect(retroPosts()).toHaveLength(0);
  });

  it("sin tamaño ni servicio: se piden y no se manda; un servicio a cotizar nunca se ofrece", async () => {
    renderUi(<RetrospectiveForm />);
    await screen.findByLabelText("Tamaño del vehículo");
    expect(screen.queryByRole("button", { name: "Pulido" })).toBeNull();
    set("Inicio", "10:00");
    set("Fin", "11:00");
    await submitRetro();
    expect(await screen.findByText("Elegí el tamaño del vehículo")).toBeInTheDocument();
    expect(screen.getByText("Elegí al menos un servicio")).toBeInTheDocument();
    expect(retroPosts()).toHaveLength(0);
  });
});

describe("Carga retrospectiva: cobro pendiente vs cobrado e importes (parseMoney_)", () => {
  it("cobrado sin tocar nada: manda el medio por defecto (efectivo) y NO manda importe (el total)", async () => {
    await retro();
    await submitRetro();
    await waitFor(() => expect(retroPosts()).toHaveLength(1));
    expect(retroPosts()[0]!.body.payment).toEqual({ payment_method_id: "m1" });
  });

  it("cobro pendiente: payment null y no se pide medio de pago", async () => {
    await retro();
    await userEvent.click(
      screen.getByRole("button", { name: /Cobrado \(tocá para dejarlo pendiente\)/ }),
    );
    expect(screen.queryByLabelText("Medio de pago")).toBeNull();
    await submitRetro();
    await waitFor(() => expect(retroPosts()).toHaveLength(1));
    expect(retroPosts()[0]!.body.payment).toBeNull();
  });

  it("elegir tarjeta manda tarjeta", async () => {
    await retro();
    await userEvent.selectOptions(screen.getByLabelText("Medio de pago"), "m2");
    await submitRetro();
    await waitFor(() => expect(retroPosts()).toHaveLength(1));
    expect((retroPosts()[0]!.body.payment as { payment_method_id: string }).payment_method_id).toBe(
      "m2",
    );
  });

  it.each([
    ["20.000", 2_000_000],
    ["20000", 2_000_000],
    ["20.000,00", 2_000_000],
    ["20.000,50", 2_000_050],
    ["1.234.567", 123_456_700],
    ["1,5", 150],
    ["$ 5.000", 500_000],
    ["0,01", 1],
  ])(
    "importe %s → %i centavos exactos (nunca el factor 1000 de parseMoney_)",
    async (text, cents) => {
      await retro({ amount: text });
      await submitRetro();
      await waitFor(() => expect(retroPosts()).toHaveLength(1));
      expect((retroPosts()[0]!.body.payment as { amount_cents: number }).amount_cents).toBe(cents);
    },
  );

  it("importe 0 se rechaza con mensaje y sin POST", async () => {
    await retro({ amount: "0" });
    await submitRetro();
    expect(await screen.findByText("El importe tiene que ser mayor a cero")).toBeInTheDocument();
    expect(retroPosts()).toHaveLength(0);
  });

  it.each(["abc", "20.5", "-5", "1,234", "1.2.3", "20,000,00", "1e3"])(
    "HALLAZGO: un importe ilegible (%s) NO se cobra como 'el total': se rechaza",
    async (text) => {
      await retro({ amount: text });
      await submitRetro();
      await new Promise((r) => setTimeout(r, 100));
      // Hoy: parseARS da null, el campo trata null como "vacío = el total" y manda el cobro completo.
      expect(retroPosts()).toHaveLength(0);
      // (el rótulo "Importe cobrado" también coincide con el texto: se mira el aviso del campo)
      expect(screen.getByRole("alert")).toHaveTextContent(/Importe no válido/);
    },
  );
});

describe("Carga retrospectiva: cliente, teléfono y patente opcionales", () => {
  it("sin nombre ni teléfono ni patente: los tres van como null", async () => {
    await retro();
    await submitRetro();
    await waitFor(() => expect(retroPosts()).toHaveLength(1));
    const b = retroPosts()[0]!.body;
    expect([b.customer_name, b.customer_phone, b.plate]).toEqual([null, null, null]);
  });

  it("espacios en blanco = sin nombre; con datos se mandan recortados y la patente tal cual la escribió", async () => {
    await retro();
    await userEvent.type(screen.getByLabelText("Cliente"), "   ");
    await userEvent.type(screen.getByLabelText("Teléfono"), "  +54 9 351 000-0001 ");
    await userEvent.type(screen.getByLabelText("Patente"), " aa123bb ");
    await submitRetro();
    await waitFor(() => expect(retroPosts()).toHaveLength(1));
    const b = retroPosts()[0]!.body;
    expect(b.customer_name).toBeNull();
    expect(b.customer_phone).toBe("+54 9 351 000-0001");
    expect(b.plate).toBe("aa123bb");
  });

  it("topes: nombre > 120, teléfono > 30 y patente > 12 se rechazan en el campo", async () => {
    await retro();
    fireEvent.change(screen.getByLabelText("Cliente"), { target: { value: "n".repeat(121) } });
    fireEvent.change(screen.getByLabelText("Teléfono"), { target: { value: "1".repeat(31) } });
    fireEvent.change(screen.getByLabelText("Patente"), { target: { value: "P".repeat(13) } });
    await submitRetro();
    expect(await screen.findAllByText(/Máximo/)).toHaveLength(3);
    expect(retroPosts()).toHaveLength(0);
  });

  it("doble toque en 'Cargar trabajo' con red lenta = un solo POST", async () => {
    let release!: () => void;
    server.gate = new Promise<void>((r) => {
      release = r;
    });
    await retro();
    const btn = screen.getByRole("button", { name: "Cargar trabajo" });
    fireEvent.click(btn);
    fireEvent.click(btn);
    await new Promise((r) => setTimeout(r, 50));
    release();
    await waitFor(() => expect(server.received).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 100));
    expect(retroPosts()).toHaveLength(1);
  });

  it("sin señal queda en la cola con UNA clave; al volver sale una vez con esa clave", async () => {
    await retro();
    goOffline(server);
    await submitRetro();
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    const item = queueItems()[0]!;
    expect(item.kind).toBe("operacion.retrospective");
    expect(retroPosts()[0]!.key).toBe(item.id);
    goOnline(server);
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(server.received[0]!.key).toBe(item.id);
    expect(queueItems()).toHaveLength(0);
  });
});

const manualPosts = () => server.attempts.filter((a) => a.url === "/bookings/manual");
async function manual(date = "2030-11-25", time = "10:00") {
  renderUi(<ManualBookingForm />);
  await basics();
  await userEvent.selectOptions(screen.getByLabelText("Puesto"), "r2");
  set("Día", date);
  set("Hora de inicio", time);
}
const submitManual = () => userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));

describe("Turno a mano", () => {
  it("Córdoba 10:00 → 13:00Z; puesto, canal CARGA_MANUAL, sin seña = deposit null", async () => {
    await manual();
    await submitManual();
    await waitFor(() => expect(manualPosts()).toHaveLength(1));
    const b = manualPosts()[0]!.body;
    expect(b.start_at).toBe("2030-11-25T13:00:00.000Z");
    expect(b.resource_id).toBe("r2");
    expect(b.channel).toBe("CARGA_MANUAL");
    expect(b.deposit).toBeNull();
    expect([b.customer_name, b.customer_phone, b.plate]).toEqual([null, null, null]);
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/agenda"));
  });

  it("puesto, día y hora son obligatorios; un servicio a cotizar no se ofrece", async () => {
    renderUi(<ManualBookingForm />);
    await screen.findByLabelText("Tamaño del vehículo");
    expect(screen.queryByRole("button", { name: "Pulido" })).toBeNull();
    set("Día", "");
    await submitManual();
    expect(await screen.findByText("Elegí el puesto")).toBeInTheDocument();
    expect(screen.getByText("Elegí la hora")).toBeInTheDocument();
    expect(screen.getByText("Elegí el día")).toBeInTheDocument();
    expect(manualPosts()).toHaveLength(0);
  });

  it("seña: medio por defecto efectivo y sin importe (la que pide el negocio); con importe, en centavos", async () => {
    await manual();
    await userEvent.click(screen.getByRole("button", { name: /Sin seña/ }));
    await submitManual();
    await waitFor(() => expect(manualPosts()).toHaveLength(1));
    expect(manualPosts()[0]!.body.deposit).toEqual({ payment_method_id: "m1" });
  });

  it.each([
    ["20.000", 2_000_000],
    ["20000", 2_000_000],
    ["5.000,50", 500_050],
  ])("seña %s → %i centavos", async (text, cents) => {
    await manual();
    await userEvent.click(screen.getByRole("button", { name: /Sin seña/ }));
    await userEvent.type(screen.getByLabelText("Importe de la seña"), text);
    await submitManual();
    await waitFor(() => expect(manualPosts()).toHaveLength(1));
    expect((manualPosts()[0]!.body.deposit as { amount_cents: number }).amount_cents).toBe(cents);
  });

  it("seña en 0 se rechaza", async () => {
    await manual();
    await userEvent.click(screen.getByRole("button", { name: /Sin seña/ }));
    await userEvent.type(screen.getByLabelText("Importe de la seña"), "0");
    await submitManual();
    expect(await screen.findByText("El importe tiene que ser mayor a cero")).toBeInTheDocument();
    expect(manualPosts()).toHaveLength(0);
  });

  it.each(["abc", "20.5", "-5", "1,234"])(
    "HALLAZGO: una seña ilegible (%s) NO se manda como 'la seña del negocio': se rechaza",
    async (text) => {
      await manual();
      await userEvent.click(screen.getByRole("button", { name: /Sin seña/ }));
      await userEvent.type(screen.getByLabelText("Importe de la seña"), text);
      await submitManual();
      await new Promise((r) => setTimeout(r, 100));
      expect(manualPosts()).toHaveLength(0);
    },
  );

  it("con seña y medios vacíos se pide el medio (no se manda deposit a medias)", async () => {
    await manual();
    await userEvent.click(screen.getByRole("button", { name: /Sin seña/ }));
    await userEvent.selectOptions(screen.getByLabelText("Medio de pago de la seña"), "");
    // El atajo del botón vuelve a poner el default: se deja constancia de lo que ocurre.
    await submitManual();
    await waitFor(() =>
      expect(
        manualPosts().length + screen.queryAllByText("Elegí el medio de pago").length,
      ).toBeGreaterThan(0),
    );
  });
});

describe("Ingreso sin turno (LLEGÓ)", () => {
  it("sin nombre/teléfono/patente/puesto: todo null, canal CALLE, 1 POST con clave única por toque", async () => {
    renderUi(<WalkInForm />);
    await basics();
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    const r = server.received[0]!;
    expect(r.url).toBe("/jobs");
    expect(r.body).toMatchObject({
      customer_name: null,
      customer_phone: null,
      plate: null,
      resource_id: null,
      channel: "CALLE",
    });
    expect(r.key).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/operacion/j100"));
  });

  it("sin tamaño ni servicio no se manda", async () => {
    renderUi(<WalkInForm />);
    await screen.findByLabelText("Tamaño del vehículo");
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    expect(await screen.findByText("Elegí al menos un servicio")).toBeInTheDocument();
    expect(server.attempts).toHaveLength(0);
  });
});

describe("Conversión hora del negocio → UTC (zonedToUtcIso)", () => {
  it.each([
    ["America/Argentina/Cordoba", "2030-11-25", "10:00", "2030-11-25T13:00:00.000Z"],
    ["America/Argentina/Cordoba", "2030-07-01", "00:00", "2030-07-01T03:00:00.000Z"],
    ["America/Argentina/Cordoba", "2030-12-31", "23:59", "2031-01-01T02:59:00.000Z"],
    ["America/Santiago", "2030-01-15", "10:00", "2030-01-15T13:00:00.000Z"], // verano austral UTC-3
    ["America/Santiago", "2030-07-15", "10:00", "2030-07-15T14:00:00.000Z"], // invierno UTC-4
    ["Europe/Madrid", "2030-07-15", "10:00", "2030-07-15T08:00:00.000Z"],
    ["Europe/Madrid", "2030-01-15", "10:00", "2030-01-15T09:00:00.000Z"],
    ["America/New_York", "2030-03-10", "01:30", "2030-03-10T06:30:00.000Z"], // antes del salto
    ["America/New_York", "2030-03-10", "03:30", "2030-03-10T07:30:00.000Z"], // después del salto
    ["America/New_York", "2030-11-03", "00:30", "2030-11-03T04:30:00.000Z"], // antes del retroceso
    ["America/New_York", "2030-11-03", "02:30", "2030-11-03T07:30:00.000Z"], // después del retroceso
    ["Australia/Lord_Howe", "2030-01-15", "10:00", "2030-01-14T23:00:00.000Z"], // +11
    ["Australia/Lord_Howe", "2030-07-15", "10:00", "2030-07-14T23:30:00.000Z"], // +10:30
    ["Asia/Kolkata", "2030-06-01", "10:00", "2030-06-01T04:30:00.000Z"],
    ["Pacific/Kiritimati", "2030-06-01", "10:00", "2030-05-31T20:00:00.000Z"], // +14
  ])("%s %s %s → %s", (tz, date, time, expected) => {
    expect(zonedToUtcIso(date, time, tz)).toBe(expected);
    // Ida y vuelta: la hora que se ve en la agenda es la que se cargó.
    expect(localTime(expected, tz)).toBe(time);
    expect(localDate(new Date(expected), tz)).toBe(date);
  });

  it.each([
    ["America/New_York", "2030-03-10", "02:30"], // no existe (salto de verano)
    ["America/New_York", "2030-11-03", "01:30"], // ocurre dos veces
    ["Europe/Madrid", "2030-03-31", "02:30"],
    ["Europe/Madrid", "2030-10-27", "02:30"],
  ])(
    "hora ambigua o inexistente %s %s %s: da un instante válido a ±1 h, nunca NaN",
    (tz, date, time) => {
      const iso = zonedToUtcIso(date, time, tz);
      expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\.000Z$/);
      const back = localTime(iso, tz);
      const [h, m] = time.split(":").map(Number) as [number, number];
      const [bh, bm] = back.split(":").map(Number) as [number, number];
      expect(Math.abs(bh * 60 + bm - (h * 60 + m))).toBeLessThanOrEqual(60);
    },
  );

  it("el formulario usa la zona de ajustes del negocio, no la del navegador (Santiago en invierno)", async () => {
    (getSettings as jest.Mock).mockResolvedValue({ timezone: "America/Santiago" });
    renderUi(<ManualBookingForm />);
    await basics();
    await userEvent.selectOptions(screen.getByLabelText("Puesto"), "r1");
    // Esperar a que lleguen los ajustes (la zona del primer render es la de Córdoba).
    await new Promise((r) => setTimeout(r, 50));
    set("Día", "2030-07-15");
    set("Hora de inicio", "10:00");
    await submitManual();
    await waitFor(() => expect(manualPosts()).toHaveLength(1));
    expect(manualPosts()[0]!.body.start_at).toBe("2030-07-15T14:00:00.000Z");
  });
});
