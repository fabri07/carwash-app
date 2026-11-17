/**
 * T3 (6.2) — Agenda: columnas por puesto + "Sin puesto", sin duplicados, zona horaria del negocio
 * (hoy/ayer cerca de medianoche), `truncated`, la carga retrospectiva en SU fecha, día vacío, muchos
 * puestos y renglones con texto hostil (XSS, largos, caracteres raros).
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgendaPageClient } from "@/features/operacion/AgendaPageClient";
import { JobPageClient } from "@/features/operacion/JobPageClient";
import { api } from "@/lib/api";
import { getSettings } from "@/services/config.service";
import {
  apiError,
  bookingEntry,
  createServer,
  day,
  entry,
  job,
  renderUi,
  resetUnits,
  TECHNICAL,
  type FakeServer,
} from "./harness";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
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

const KEEP_REAL = [
  "setTimeout",
  "clearTimeout",
  "setInterval",
  "clearInterval",
  "setImmediate",
  "clearImmediate",
  "nextTick",
  "queueMicrotask",
  "requestAnimationFrame",
  "cancelAnimationFrame",
  "requestIdleCallback",
  "cancelIdleCallback",
  "performance",
  "hrtime",
] as const;

let server: FakeServer;
beforeEach(() => {
  resetUnits();
  server = createServer([job()]);
});
afterEach(() => jest.useRealTimers());

const dayCalls = () =>
  (api.get as jest.Mock).mock.calls
    .filter((c) => c[0] === "/agenda/day")
    .map((c) => (c[1] as { params: { date: string } }).params.date);

describe("Columnas por puesto y 'Sin puesto'", () => {
  it("una columna por puesto, en orden, y los renglones sin puesto o de un puesto desconocido van a 'Sin puesto'", async () => {
    server.defaultDay = day([
      entry({
        id: "e1",
        job_id: "j1",
        resource_id: "r1",
        customer: { id: "c", name: "En P1", phone_e164: null },
      }),
      entry({
        id: "e2",
        job_id: "j2",
        resource_id: "r2",
        customer: { id: "c", name: "En P2", phone_e164: null },
      }),
      entry({
        id: "e3",
        job_id: "j3",
        resource_id: null,
        customer: { id: "c", name: "Suelto", phone_e164: null },
      }),
      entry({
        id: "e4",
        job_id: "j4",
        resource_id: "borrado",
        customer: { id: "c", name: "Puesto viejo", phone_e164: null },
      }),
    ]);
    renderUi(<AgendaPageClient />);
    const p1 = await screen.findByRole("region", { name: "Puesto Puesto 1" });
    const p2 = screen.getByRole("region", { name: "Puesto Puesto 2" });
    const sin = screen.getByRole("region", { name: "Puesto Sin puesto" });
    expect(within(p1).getByText(/En P1/)).toBeInTheDocument();
    expect(within(p1).queryByText(/En P2|Suelto|Puesto viejo/)).toBeNull();
    expect(within(p2).getByText(/En P2/)).toBeInTheDocument();
    expect(within(sin).getByText(/Suelto/)).toBeInTheDocument();
    expect(within(sin).getByText(/Puesto viejo/)).toBeInTheDocument();
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Puesto 1", "Puesto 2", "Sin puesto"]);
  });

  it("sin ningún renglón suelto no aparece la columna 'Sin puesto'", async () => {
    renderUi(<AgendaPageClient />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    expect(screen.queryByRole("region", { name: "Puesto Sin puesto" })).toBeNull();
  });

  it("jobs y turnos se muestran una sola vez cada uno (mismo id en distinto tipo no choca)", async () => {
    server.defaultDay = day([
      entry({ id: "same", kind: "JOB", job_id: "j1", booking_id: "b1" }),
      bookingEntry({ id: "same", booking_id: "b2", resource_id: "r1" }),
      bookingEntry({ id: "b3", booking_id: "b3", resource_id: "r2" }),
    ]);
    const err = jest.spyOn(console, "error").mockImplementation(() => undefined);
    const { container } = renderUi(<AgendaPageClient />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    const hrefs = Array.from(container.querySelectorAll("a[href^='/operacion/']")).map((a) =>
      a.getAttribute("href"),
    );
    // 3 renglones = 3 enlaces (más los 3 accesos de arriba, que no cuelgan de un puesto).
    const cards = hrefs.filter((h) => /\/operacion\/(j1|turno\/)/.test(h ?? ""));
    expect(cards).toHaveLength(3);
    expect(new Set(cards).size).toBe(3);
    expect(err.mock.calls.filter((c) => /same key|unique "key"/i.test(String(c[0])))).toHaveLength(
      0,
    );
    err.mockRestore();
  });

  it("un renglón de turno lleva a /operacion/turno/{id}?date=<día visto>, un job a /operacion/{id}", async () => {
    server.defaultDay = day([
      bookingEntry({ booking_id: "b9" }),
      entry({ id: "e2", job_id: "j7" }),
    ]);
    renderUi(<AgendaPageClient initialDate="2030-11-25" />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/operacion/turno/b9?date=2030-11-25");
    expect(hrefs).toContain("/operacion/j7");
  });

  it("agrupa por hora del NEGOCIO (13:00Z = 10:00 en Córdoba) y ordena dentro de la columna", async () => {
    server.defaultDay = day([
      entry({
        id: "tarde",
        job_id: "j2",
        start_at: "2030-11-25T21:30:00Z",
        end_at: "2030-11-25T22:30:00Z",
      }),
      entry({
        id: "temprano",
        job_id: "j1",
        start_at: "2030-11-25T13:00:00Z",
        end_at: "2030-11-25T14:00:00Z",
      }),
    ]);
    renderUi(<AgendaPageClient />);
    const col = await screen.findByRole("region", { name: "Puesto Puesto 1" });
    expect(within(col).getByText("10:00–11:00")).toBeInTheDocument();
    expect(within(col).getByText("18:30–19:30")).toBeInTheDocument();
    const text = col.textContent!;
    expect(text.indexOf("10:00–11:00")).toBeLessThan(text.indexOf("18:30–19:30"));
  });
});

describe("Zona horaria del negocio: hoy y ayer", () => {
  const at = (iso: string) => {
    jest.useFakeTimers({ doNotFake: [...KEEP_REAL] });
    jest.setSystemTime(new Date(iso));
  };

  it("23:30 en Córdoba (02:30Z del día siguiente): 'hoy' sigue siendo el día de Córdoba", async () => {
    at("2026-11-18T02:30:00Z");
    renderUi(<AgendaPageClient />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    expect(dayCalls().at(-1)).toBe("2026-11-17");
    expect(screen.getByRole("button", { name: "Hoy" }).className).toMatch(/bg-primary/);
    await userEvent.click(screen.getByRole("button", { name: "Ayer" }));
    await waitFor(() => expect(dayCalls().at(-1)).toBe("2026-11-16"));
  });

  it("00:30 en Córdoba (03:30Z): ya es el día siguiente", async () => {
    at("2026-11-18T03:30:00Z");
    renderUi(<AgendaPageClient />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    expect(dayCalls().at(-1)).toBe("2026-11-18");
  });

  it("zona del negocio distinta a la del navegador (Auckland): hoy sale de la zona del negocio", async () => {
    (getSettings as jest.Mock).mockResolvedValue({ timezone: "Pacific/Auckland" });
    at("2026-11-17T12:00:00Z"); // Auckland: 01:00 del 18
    server.days["2026-11-18"] = day([entry({ id: "auck", job_id: "ja" })], {
      timezone: "Pacific/Auckland",
    });
    renderUi(<AgendaPageClient />);
    await waitFor(() => expect(dayCalls()).toContain("2026-11-18"));
    expect(await screen.findByText(/Lavado completo/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hoy" }).className).toMatch(/bg-primary/);
  });

  it("cambio de año y de mes con 'Día anterior' / 'Día siguiente' y la fecha escrita", async () => {
    renderUi(<AgendaPageClient initialDate="2030-01-01" />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    await userEvent.click(screen.getByRole("button", { name: "Día anterior" }));
    await waitFor(() => expect(dayCalls().at(-1)).toBe("2029-12-31"));
    await userEvent.click(screen.getByRole("button", { name: "Día siguiente" }));
    await userEvent.click(screen.getByRole("button", { name: "Día siguiente" }));
    await waitFor(() => expect(dayCalls().at(-1)).toBe("2030-01-02"));
    fireEvent.change(screen.getByLabelText("Ir a un día"), { target: { value: "2028-02-29" } });
    await waitFor(() => expect(dayCalls().at(-1)).toBe("2028-02-29"));
    fireEvent.change(screen.getByLabelText("Ir a un día"), { target: { value: "" } });
    expect(dayCalls().at(-1)).toBe("2028-02-29"); // vaciar el campo no pide "undefined"
  });

  it.each(["2026-02-30", "garbage", "2026-13-01", "<script>", "2026-1-1"])(
    "una fecha inválida en la URL (%s) cae en hoy y no se manda al servidor",
    async (bad) => {
      renderUi(<AgendaPageClient initialDate={bad} />);
      await screen.findByRole("region", { name: "Puesto Puesto 1" });
      expect(dayCalls()).not.toContain(bad);
      expect(dayCalls().every((d) => /^\d{4}-\d{2}-\d{2}$/.test(d))).toBe(true);
    },
  );

  it("HALLAZGO (bajo): con la pantalla abierta al cruzar la medianoche, 'hoy' se actualiza solo", async () => {
    // El reloj de la pantalla es un setInterval (cada 30 s): acá se falsea para poder avanzarlo.
    jest.useFakeTimers({ doNotFake: KEEP_REAL.filter((k) => k !== "setInterval") });
    jest.setSystemTime(new Date("2026-11-18T02:59:30Z")); // 23:59:30 en Córdoba
    renderUi(<AgendaPageClient />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    expect(dayCalls().at(-1)).toBe("2026-11-17");
    await act(async () => {
      jest.advanceTimersByTime(5 * 60_000); // 00:04:30 del 18
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 50));
    // Un lavadero abre la agenda de noche y la deja en el celular: a la mañana sigue mostrando ayer.
    expect(dayCalls().at(-1)).toBe("2026-11-18");
  });
});

describe("Estados de la lista", () => {
  it("truncated: avisa que se muestran los primeros y sigue mostrando los renglones", async () => {
    server.defaultDay = day([entry()], { truncated: true });
    renderUi(<AgendaPageClient />);
    expect(await screen.findByText(/se muestran los primeros/)).toBeInTheDocument();
    expect(screen.getByText(/Cliente Demo/)).toBeInTheDocument();
  });

  it("día vacío con puestos: avisa y deja las columnas; sin puestos ni turnos: dice que no hay puestos", async () => {
    server.defaultDay = day([]);
    const a = renderUi(<AgendaPageClient />);
    expect(await screen.findByText("No hay turnos ni trabajos este día.")).toBeInTheDocument();
    expect(screen.getAllByText("Sin turnos ni trabajos.")).toHaveLength(2);
    a.unmount();
    server.defaultDay = day([], { resources: [] });
    renderUi(<AgendaPageClient />);
    expect(await screen.findByText(/No hay puestos configurados/)).toBeInTheDocument();
  });

  it("sin puestos pero con renglones: todo va a 'Sin puesto' (no se pierde ninguno)", async () => {
    server.defaultDay = day(
      [entry({ id: "a", job_id: "ja" }), entry({ id: "b", job_id: "jb", resource_id: null })],
      {
        resources: [],
      },
    );
    renderUi(<AgendaPageClient />);
    const col = await screen.findByRole("region", { name: "Puesto Sin puesto" });
    expect(within(col).getAllByText(/Cliente Demo/)).toHaveLength(2);
  });

  it("falla la carga: mensaje en español sin texto técnico; luego se puede cambiar de día", async () => {
    server.failGet.push({
      match: /^\/agenda\/day$/,
      error: () => apiError(500, undefined, "Traceback psycopg2"),
    });
    renderUi(<AgendaPageClient />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(TECHNICAL);
    expect(document.body.textContent).not.toContain("psycopg2");
    expect(screen.getByRole("button", { name: "Ayer" })).toBeEnabled();
  });

  it("40 puestos: 40 columnas dentro de un contenedor que se desliza de lado, cada una sin encogerse", async () => {
    const resources = Array.from({ length: 40 }, (_, i) => ({
      id: `r${i}`,
      name: `Puesto ${i + 1}`,
    }));
    server.defaultDay = day([entry({ resource_id: "r39" })], { resources });
    renderUi(<AgendaPageClient />);
    const board = await screen.findByTestId("day-board");
    expect(board.className).toMatch(/overflow-x-auto/);
    const cols = within(board).getAllByRole("region");
    expect(cols).toHaveLength(40);
    for (const c of cols) expect(c.className).toMatch(/shrink-0/);
    expect(within(cols[39]!).getByText(/Cliente Demo/)).toBeInTheDocument();
  });

  it("respuestas fuera de orden: si la de ayer llega tarde no pisa la de hoy", async () => {
    let releaseYesterday!: () => void;
    const slow = new Promise<void>((r) => {
      releaseYesterday = r;
    });
    const get = api.get as jest.Mock;
    const base = get.getMockImplementation()!;
    get.mockImplementation(async (url: string, cfg?: { params?: { date?: string } }) => {
      if (url === "/agenda/day" && cfg?.params?.date === "2030-11-24") {
        await slow;
        return {
          data: day(
            [
              entry({
                id: "viejo",
                job_id: "jv",
                customer: { id: "c", name: "DE AYER", phone_e164: null },
              }),
            ],
            { date: "2030-11-24" },
          ),
        };
      }
      return base(url, cfg);
    });
    renderUi(<AgendaPageClient initialDate="2030-11-24" />);
    await userEvent.click(screen.getByRole("button", { name: "Día siguiente" }));
    await screen.findByText(/Cliente Demo/);
    releaseYesterday();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(/DE AYER/)).toBeNull();
    expect(screen.getByText(/Cliente Demo/)).toBeInTheDocument();
  });
});

describe("La carga retrospectiva cae en la agenda de SU fecha", () => {
  it("aparece (con 'Carga manual') el día cargado y no en la de hoy", async () => {
    const retro = entry({
      id: "retro",
      job_id: "jr",
      kind: "JOB",
      job_status: "COBRADO",
      booking_id: null,
      start_at: "2026-11-10T13:00:00Z",
      end_at: "2026-11-10T14:00:00Z",
      entry_mode: "RETROSPECTIVA",
      customer: { id: "c", name: "Cargado después", phone_e164: null },
    });
    server.days["2026-11-10"] = day([retro], { date: "2026-11-10" });
    const a = renderUi(<AgendaPageClient initialDate="2026-11-10" />);
    expect(await screen.findByText(/Cargado después/)).toBeInTheDocument();
    expect(screen.getByText("Carga manual")).toBeInTheDocument();
    a.unmount();
    renderUi(<AgendaPageClient />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    expect(screen.queryByText(/Cargado después/)).toBeNull();
  });
});

describe("Texto hostil en un renglón", () => {
  const evil = [
    '<img src=x onerror="window.__xss=1">',
    "<script>window.__xss=1</script>",
    '"><svg/onload=window.__xss=1>',
    "javascript:alert(1)",
    "{{constructor.constructor('alert(1)')()}}",
  ];

  it.each(evil)(
    "nombre, patente y servicio con %s se muestran como texto, sin inyectar HTML",
    async (bad) => {
      server.defaultDay = day([
        entry({
          customer: { id: "c", name: bad, phone_e164: null },
          vehicle_plate: bad,
          services: [bad],
          code: bad,
        }),
      ]);
      const { container } = renderUi(<AgendaPageClient />);
      await screen.findByRole("region", { name: "Puesto Puesto 1" });
      expect(container.querySelector("img, script, svg[onload], iframe")).toBeNull();
      expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
      expect(container.textContent).toContain(bad);
    },
  );

  it("la pantalla del job y su título también escapan el nombre", async () => {
    server = createServer([
      job({ customer: { id: "c", name: evil[0]!, phone_e164: null }, vehicle_plate: evil[1]! }),
    ]);
    const { container } = renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("heading", { level: 1 });
    expect(container.querySelector("img, script")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain(evil[0]);
  });

  it("caracteres raros (emoji, RTL, ceros de ancho cero, saltos de línea) no rompen el renglón", async () => {
    const weird = "Zoë 🚗‮fdp​\u0000\n\tÑandú";
    server.defaultDay = day([
      entry({ customer: { id: "c", name: weird, phone_e164: null }, vehicle_plate: "AA‮000" }),
    ]);
    renderUi(<AgendaPageClient />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    expect(document.body.textContent).toContain("Zoë 🚗");
  });

  it("HALLAZGO (bajo): un nombre de 300 caracteres sin espacios no desborda la columna (necesita break-words/truncate)", async () => {
    const long = "X".repeat(300);
    server.defaultDay = day([entry({ customer: { id: "c", name: long, phone_e164: null } })]);
    renderUi(<AgendaPageClient />);
    await screen.findByRole("region", { name: "Puesto Puesto 1" });
    const p = screen.getByText(new RegExp(long)) as HTMLElement;
    const chain: string[] = [];
    for (let n: HTMLElement | null = p; n; n = n.parentElement) chain.push(n.className);
    // La columna mide 88 % del ancho y no se encoge: sin esto el texto se sale de la tarjeta.
    expect(chain.join(" ")).toMatch(/break-words|break-all|truncate|overflow-hidden|overflow-wrap/);
  });

  it("renglón sin cliente, sin servicios y turno sin saldo: textos por defecto, sin 'Debe' ni NaN", async () => {
    server.defaultDay = day([
      bookingEntry({
        customer: null,
        services: [],
        vehicle_plate: null,
        balance_due_cents: null,
        total_cents: null,
      }),
    ]);
    renderUi(<AgendaPageClient />);
    await screen.findByText("Sin servicio");
    expect(screen.getByText("Sin nombre")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Debe|NaN|undefined|null/);
  });
});
