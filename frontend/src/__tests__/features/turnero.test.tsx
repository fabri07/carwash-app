import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import TurneroLayout from "@/app/(turnero)/[slug]/layout";
import TurneroPage from "@/app/(turnero)/[slug]/page";
import MiTurnoPage from "@/app/(turnero)/[slug]/mi-turno/page";
import { BookingFlow } from "@/features/turnero/BookingFlow";
import { MyBooking } from "@/features/turnero/MyBooking";
import { TurneroShell } from "@/features/turnero/TurneroShell";
import {
  bookableDays,
  dayLabel,
  depositMessage,
  priceLabel,
  statusLabel,
  turneroErrorMessage,
  whatsappLink,
  whenLabel,
} from "@/features/turnero/format";
import { retryUnlessClientError } from "@/features/turnero/queries";
import {
  cancelBooking,
  createBooking,
  getAvailability,
  getBusiness,
  getCatalog,
  lookupBooking,
} from "@/services/public.service";
import type {
  TurneroAvailability,
  TurneroBooking,
  TurneroBusiness,
  TurneroCatalog,
} from "@/types/api";

jest.mock("@/services/public.service", () => ({
  getBusiness: jest.fn(),
  getCatalog: jest.fn(),
  getAvailability: jest.fn(),
  createBooking: jest.fn(),
  lookupBooking: jest.fn(),
  cancelBooking: jest.fn(),
}));

jest.setTimeout(30_000);

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.setPointerCapture = () => undefined;
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

const axiosConfig = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, code?: string) {
  const response = {
    status,
    data: code ? { detail: { code, message: "x" } } : {},
    statusText: "",
    headers: {},
    config: axiosConfig,
  } as AxiosResponse;
  return new AxiosError("x", "ERR_BAD_REQUEST", axiosConfig, null, response);
}

const TZ = "America/Argentina/Cordoba";
const color = (hex: string) => ({ oklch: "oklch(50% 0.1 200)", hex });
const negocio: TurneroBusiness = {
  slug: "lavadero-norte",
  name: "Lavadero Norte",
  timezone: TZ,
  currency: "ARS",
  palette: {
    code: "oceano",
    name: "Océano",
    background: color("#f5fbff"),
    primary: color("#0a7db2"),
    secondary: color("#33aa77"),
    on_primary: color("#ffffff"),
  },
  font: { code: "default", name: "Sistema", family: null, description: "" },
  logo_url: null,
  welcome_text: "¡Bienvenido!",
  address: "Calle Falsa 123",
  whatsapp_e164: "+5493510009999",
  cancellation_policy: "Cancelá con 12 h de anticipación.",
  deposit_enabled: true,
  deposit_instructions: null,
  hold_min: 30,
  booking_horizon_days: 3,
};
const catalogo: TurneroCatalog = {
  vehicle_sizes: [
    { id: "auto", label: "Auto" },
    { id: "suv", label: "SUV" },
  ],
  services: [
    {
      id: "lavado",
      name: "Lavado completo",
      category_name: "Lavados",
      pricing_mode: "PRECIO_FIJO",
      bookable: true,
      deposit_bps: 2000,
      prices: [
        {
          vehicle_size_id: "auto",
          price_cents: 2_000_000,
          duration_min: 60,
          deposit_cents: 400_000,
        },
      ],
    },
    {
      id: "tapizado",
      name: "Tapizado",
      category_name: null,
      pricing_mode: "A_COTIZAR",
      bookable: false,
      deposit_bps: 2000,
      prices: [
        { vehicle_size_id: "auto", price_cents: 1_500_000, duration_min: 120, deposit_cents: 0 },
      ],
    },
  ],
};
const dispo: TurneroAvailability = {
  date: "2026-11-23",
  timezone: TZ,
  duration_min: 60,
  slots: [
    { start_at: "2026-11-23T12:00:00Z", end_at: "2026-11-23T13:00:00Z" },
    { start_at: "2026-11-23T13:00:00Z", end_at: "2026-11-23T14:00:00Z" },
  ],
};
const turno: TurneroBooking = {
  code: "TUR-K7M2QX",
  status: "PENDIENTE_SEÑA",
  start_at: "2026-11-23T13:00:00Z",
  end_at: "2026-11-23T14:00:00Z",
  timezone: TZ,
  vehicle_size_label: "Auto",
  services: [{ name: "Lavado completo", duration_min: 60, price_cents: 2_000_000 }],
  total_cents: 2_000_000,
  deposit_cents: 400_000,
  hold_expires_at: "2026-11-21T13:30:00Z",
  can_cancel: true,
};

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function conMarco(ui: React.ReactElement) {
  return wrap(<TurneroShell slug="lavadero-norte">{ui}</TurneroShell>);
}

beforeEach(() => {
  jest.mocked(getBusiness).mockReset().mockResolvedValue(negocio);
  jest.mocked(getCatalog).mockReset().mockResolvedValue(catalogo);
  jest.mocked(getAvailability).mockReset().mockResolvedValue(dispo);
  jest.mocked(createBooking).mockReset().mockResolvedValue(turno);
  jest.mocked(lookupBooking).mockReset().mockResolvedValue(turno);
  jest.mocked(cancelBooking).mockReset();
  document.documentElement.removeAttribute("style");
});

async function elegirHastaDatos(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Auto" }));
  await user.click(screen.getByRole("checkbox"));
  expect(screen.getByText(/Total \$ 20\.000,00 · 1 h/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Elegir día" }));
  const dias = screen.getAllByRole("button", { pressed: false });
  await user.click(dias[dias.length - 1]!);
  await user.click(await screen.findByRole("button", { name: "10:00" }));
}

async function completarDatos(user: ReturnType<typeof userEvent.setup>, telefono = "351 000 0001") {
  await user.type(screen.getByLabelText("Nombre"), "Cliente Web");
  await user.type(screen.getByLabelText("WhatsApp"), telefono);
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Reservar" }));
}

describe("turnero: reservar (B13)", () => {
  it("recorre tamaño → servicios → día → horario → datos → confirmación con seña", async () => {
    const user = userEvent.setup();
    conMarco(<BookingFlow slug="lavadero-norte" />);
    expect(await screen.findByText("Lavadero Norte")).toBeInTheDocument();
    expect(await screen.findByText("¡Bienvenido!")).toBeInTheDocument();
    // La marca del negocio queda en <html>.
    await waitFor(() =>
      expect(document.documentElement.style.getPropertyValue("--primary")).not.toBe(""),
    );
    expect(document.documentElement.style.getPropertyValue("--brand-secondary")).toBe(
      "oklch(50% 0.1 200)",
    );

    await elegirHastaDatos(user);
    expect(jest.mocked(getAvailability)).toHaveBeenCalledWith(
      "lavadero-norte",
      expect.objectContaining({ vehicleSizeId: "auto", serviceIds: ["lavado"] }),
    );
    expect(screen.getByText("Cancelá con 12 h de anticipación.")).toBeInTheDocument();
    await completarDatos(user);

    await screen.findByText("Tu turno está reservado");
    const [slug, payload, clave] = jest.mocked(createBooking).mock.calls[0]!;
    expect(slug).toBe("lavadero-norte");
    expect(payload).toEqual({
      vehicle_size_id: "auto",
      service_ids: ["lavado"],
      start_at: "2026-11-23T13:00:00Z",
      name: "Cliente Web",
      phone: "351 000 0001",
      plate: null,
      accept_policy: true,
    });
    expect(clave).toEqual(expect.any(String));
    expect(screen.getByText("TUR-K7M2QX")).toBeInTheDocument();
    expect(screen.getByText("Coordiná el pago de la seña por WhatsApp.")).toBeInTheDocument();
    const whatsapp = screen.getByRole("link", { name: /Enviar comprobante por WhatsApp/ });
    expect(whatsapp.getAttribute("href")).toMatch(/^https:\/\/wa\.me\/5493510009999\?text=/);
    expect(decodeURIComponent(whatsapp.getAttribute("href")!)).toContain("TUR-K7M2QX");
    expect(screen.getByRole("link", { name: "Mi turno" })).toBeInTheDocument();
  });

  it("muestra las instrucciones de seña del negocio y la lista de espera sin seña", async () => {
    jest
      .mocked(getBusiness)
      .mockResolvedValue({ ...negocio, deposit_instructions: "Alias: lavadero.norte" });
    jest.mocked(createBooking).mockResolvedValueOnce(turno);
    const user = userEvent.setup();
    const { unmount } = conMarco(<BookingFlow slug="lavadero-norte" />);
    await elegirHastaDatos(user);
    await completarDatos(user);
    expect(await screen.findByText("Alias: lavadero.norte")).toBeInTheDocument();
    unmount();

    jest.mocked(createBooking).mockResolvedValueOnce({
      ...turno,
      status: "PENDIENTE_REVISION",
      deposit_cents: 0,
      hold_expires_at: null,
    });
    conMarco(<BookingFlow slug="lavadero-norte" />);
    await elegirHastaDatos(user);
    await completarDatos(user);
    expect(await screen.findByText("Recibimos tu pedido")).toBeInTheDocument();
    expect(screen.queryByText(/pagá la seña/)).not.toBeInTheDocument();
  });

  it("un servicio a cotizar se muestra con 'desde' y consulta por WhatsApp, sin reserva", async () => {
    const user = userEvent.setup();
    conMarco(<BookingFlow slug="lavadero-norte" />);
    await user.click(await screen.findByRole("button", { name: "Auto" }));
    expect(screen.getByText("desde $ 15.000,00")).toBeInTheDocument();
    const consulta = screen.getByRole("link", { name: /Consultar por WhatsApp/ });
    expect(decodeURIComponent(consulta.getAttribute("href")!)).toContain("Tapizado para Auto");
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Elegir día" })).toBeDisabled();
  });

  it("si el horario se ocupó vuelve a la lista de horarios con el aviso", async () => {
    jest.mocked(createBooking).mockRejectedValueOnce(apiError(409, "SLOT_TAKEN"));
    const user = userEvent.setup();
    conMarco(<BookingFlow slug="lavadero-norte" />);
    await elegirHastaDatos(user);
    await completarDatos(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("Ese horario se acaba de ocupar");
    expect(screen.getByRole("heading", { name: /Horarios del/ })).toBeInTheDocument();
    await waitFor(() => expect(jest.mocked(getAvailability).mock.calls.length).toBeGreaterThan(1));
  });

  it("un WhatsApp inválido se marca en el campo y el tope de turnos arriba del botón", async () => {
    jest
      .mocked(createBooking)
      .mockRejectedValueOnce(apiError(422, "INVALID_PHONE"))
      .mockRejectedValueOnce(apiError(409, "TOO_MANY_ACTIVE_BOOKINGS"));
    const user = userEvent.setup();
    conMarco(<BookingFlow slug="lavadero-norte" />);
    await elegirHastaDatos(user);
    await completarDatos(user, "54 351 000 0001");
    expect(await screen.findByText(/celular argentino con código de área/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reservar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ya tenés dos turnos");
  });

  it("valida los datos antes de mandar", async () => {
    const user = userEvent.setup();
    conMarco(<BookingFlow slug="lavadero-norte" />);
    await elegirHastaDatos(user);
    await user.type(screen.getByLabelText("WhatsApp"), "123");
    await user.click(screen.getByRole("button", { name: "Reservar" }));
    expect(await screen.findByText("Ingresá tu nombre.")).toBeInTheDocument();
    expect(screen.getByText("Ingresá el número con código de área.")).toBeInTheDocument();
    expect(screen.getByText("Tenés que aceptar la política de cancelación.")).toBeInTheDocument();
    expect(jest.mocked(createBooking)).not.toHaveBeenCalled();
  });

  it("vuelve atrás entre pasos y avisa si no hay horarios", async () => {
    jest.mocked(getAvailability).mockResolvedValue({ ...dispo, slots: [] });
    const user = userEvent.setup();
    conMarco(<BookingFlow slug="lavadero-norte" />);
    await user.click(await screen.findByRole("button", { name: "Auto" }));
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Elegir día" }));
    await user.click(screen.getAllByRole("button", { pressed: false }).at(-1)!);
    expect(await screen.findByText(/No quedan horarios libres/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Volver" }));
    expect(screen.getByRole("heading", { name: "¿Qué día?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Volver" }));
    await user.click(screen.getByRole("button", { name: "Volver" }));
    expect(screen.getByRole("heading", { name: "¿Qué vehículo traés?" })).toBeInTheDocument();
    // Cambiar de tamaño limpia los servicios elegidos.
    await user.click(screen.getByRole("button", { name: "SUV" }));
    expect(screen.getByText("No hay servicios para este vehículo.")).toBeInTheDocument();
  });

  it("un negocio inexistente muestra el mismo 'no encontrado'", async () => {
    jest.mocked(getBusiness).mockRejectedValue(apiError(404, "NOT_FOUND"));
    conMarco(<BookingFlow slug="no-existe" />);
    expect(await screen.findByText("No encontramos este negocio")).toBeInTheDocument();
  });

  it("si el catálogo falla lo dice", async () => {
    // 4xx: sin reintentos (un 5xx reintentaría con espera).
    jest.mocked(getCatalog).mockRejectedValue(apiError(422));
    conMarco(<BookingFlow slug="lavadero-norte" />);
    expect(await screen.findByText(/Algo salió mal/)).toBeInTheDocument();
  });
});

describe("turnero: Mi turno (D5-8)", () => {
  it("busca el turno con código y WhatsApp y lo cancela", async () => {
    jest.mocked(cancelBooking).mockResolvedValue({
      code: "TUR-K7M2QX",
      status: "CANCELADO_CLIENTE",
      classification: "TARDIA",
      deposit_status: "EN_REVISION",
    });
    const user = userEvent.setup();
    conMarco(<MyBooking slug="lavadero-norte" />);
    await user.type(await screen.findByLabelText("Código del turno"), "tur-k7m2qx");
    await user.type(screen.getByLabelText("WhatsApp con el que reservaste"), "351 000 0001");
    await user.click(screen.getByRole("button", { name: "Ver mi turno" }));
    expect(jest.mocked(lookupBooking)).toHaveBeenCalledWith("lavadero-norte", {
      code: "tur-k7m2qx",
      phone: "351 000 0001",
    });
    expect(await screen.findByText("TUR-K7M2QX")).toBeInTheDocument();
    expect(screen.getByText("Pendiente de seña")).toBeInTheDocument();

    jest.mocked(lookupBooking).mockResolvedValue({
      ...turno,
      status: "CANCELADO_CLIENTE",
      can_cancel: false,
    });
    await user.click(screen.getByRole("button", { name: "Quiero cancelar" }));
    await user.type(screen.getByLabelText("Motivo (opcional)"), "Me surgió algo");
    await user.click(screen.getByRole("button", { name: "Cancelar el turno" }));
    expect(jest.mocked(cancelBooking)).toHaveBeenCalledWith("lavadero-norte", {
      code: "tur-k7m2qx",
      phone: "351 000 0001",
      reason: "Me surgió algo",
    });
    const estado = await screen.findByRole("status");
    expect(within(estado).getByText("Turno cancelado.")).toBeInTheDocument();
    expect(within(estado).getByText(/puede retener la seña/)).toBeInTheDocument();
    expect(within(estado).getByText(/revisar qué pasa con la seña/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quiero cancelar" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Buscar otro turno" }));
    expect(screen.getByLabelText("Código del turno")).toBeInTheDocument();
  });

  it("código o WhatsApp que no coinciden dan el mismo 'no encontramos'", async () => {
    jest.mocked(lookupBooking).mockRejectedValue(apiError(404, "NOT_FOUND"));
    const user = userEvent.setup();
    conMarco(<MyBooking slug="lavadero-norte" />);
    await user.type(await screen.findByLabelText("Código del turno"), "TUR-ZZZZZZ");
    await user.type(screen.getByLabelText("WhatsApp con el que reservaste"), "351 000 0002");
    await user.click(screen.getByRole("button", { name: "Ver mi turno" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No encontramos un turno");
  });

  it("si la cancelación falla lo dice en el formulario", async () => {
    jest.mocked(cancelBooking).mockRejectedValue(apiError(409, "CONFLICT"));
    const user = userEvent.setup();
    conMarco(<MyBooking slug="lavadero-norte" />);
    await user.type(await screen.findByLabelText("Código del turno"), "TUR-K7M2QX");
    await user.type(screen.getByLabelText("WhatsApp con el que reservaste"), "351 000 0001");
    await user.click(screen.getByRole("button", { name: "Ver mi turno" }));
    await user.click(await screen.findByRole("button", { name: "Quiero cancelar" }));
    await user.click(screen.getByRole("button", { name: "Cancelar el turno" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ya no se puede cancelar");
    expect(jest.mocked(cancelBooking).mock.calls[0]![1]).toMatchObject({ reason: null });
  });
});

describe("turnero: rutas", () => {
  it("el layout y las páginas reciben el slug", async () => {
    const layout = await TurneroLayout({
      children: await TurneroPage({ params: Promise.resolve({ slug: "lavadero-norte" }) }),
      params: Promise.resolve({ slug: "lavadero-norte" }),
    });
    wrap(layout);
    expect(await screen.findByRole("button", { name: "Auto" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Mi turno/ })).toHaveAttribute(
      "href",
      "/lavadero-norte/mi-turno",
    );
  });

  it("Mi turno es su propia página", async () => {
    wrap(await MiTurnoPage({ params: Promise.resolve({ slug: "lavadero-norte" }) }));
    expect(await screen.findByLabelText("Código del turno")).toBeInTheDocument();
  });

  it("con logo muestra la imagen", async () => {
    jest.mocked(getBusiness).mockResolvedValue({
      ...negocio,
      logo_url: "https://cdn.example.com/logo.png",
      address: null,
    });
    const { container } = conMarco(<p>contenido</p>);
    await screen.findByText("Lavadero Norte");
    expect(container.querySelector("img")?.getAttribute("src")).toBe(
      "https://cdn.example.com/logo.png",
    );
  });
});

describe("turnero: helpers", () => {
  it("los días reservables arrancan hoy en la zona del negocio e incluyen el horizonte", () => {
    // 23/11 02:00 UTC = 22/11 23:00 en Córdoba.
    const dias = bookableDays(new Date("2026-11-23T02:00:00Z"), TZ, 2);
    expect(dias).toEqual(["2026-11-22", "2026-11-23", "2026-11-24"]);
    expect(bookableDays(new Date("2026-12-31T12:00:00Z"), TZ, 1)).toEqual([
      "2026-12-31",
      "2027-01-01",
    ]);
  });

  it("formatea días, horas y precios", () => {
    expect(dayLabel("2026-11-23")).toBe("lun 23/11");
    expect(whenLabel("2026-11-23T13:00:00Z", TZ)).toBe("lun 23/11 · 10:00");
    expect(priceLabel(catalogo.services[0]!, "auto")).toBe("$ 20.000,00");
    expect(priceLabel(catalogo.services[1]!, "auto")).toBe("desde $ 15.000,00");
    expect(priceLabel(catalogo.services[0]!, "suv")).toBe("");
    expect(statusLabel("CONFIRMADO")).toBe("Confirmado");
    expect(statusLabel("OTRO")).toBe("OTRO");
  });

  it("arma el link de WhatsApp y el mensaje de la seña", () => {
    expect(whatsappLink(null, "x")).toBeNull();
    expect(whatsappLink("+54 9 351", "hola mundo")).toBe("https://wa.me/549351?text=hola%20mundo");
    expect(depositMessage(turno)).toContain("comprobante de la seña de $ 4.000,00");
    expect(depositMessage({ ...turno, deposit_cents: 0 })).toContain("Quería confirmarlo.");
  });

  it("traduce los errores por código y por estado", () => {
    expect(turneroErrorMessage(new Error("x"))).toMatch(/Algo salió mal/);
    expect(turneroErrorMessage(new AxiosError("red"))).toMatch(/No hay conexión/);
    expect(turneroErrorMessage(apiError(429))).toMatch(/demasiados intentos/);
    expect(turneroErrorMessage(apiError(404))).toMatch(/No encontramos un turno/);
    expect(turneroErrorMessage(apiError(422, "SERVICE_NOT_BOOKABLE"))).toMatch(/online/);
    expect(turneroErrorMessage(apiError(500))).toMatch(/Algo salió mal/);
  });

  it("no reintenta un 4xx", () => {
    expect(retryUnlessClientError(0, apiError(404))).toBe(false);
    expect(retryUnlessClientError(0, apiError(500))).toBe(true);
    expect(retryUnlessClientError(2, new Error("x"))).toBe(false);
  });
});
