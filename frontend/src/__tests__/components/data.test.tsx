import { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PeriodFilter } from "@/components/data/PeriodFilter";
import { SmartTable, type SmartColumn } from "@/components/data/SmartTable";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { MoneyInput } from "@/components/ui/money-input";
import { Stepper } from "@/components/ui/stepper";
import { downloadCSV } from "@/lib/csv";
import type { PeriodValue } from "@/lib/period";

jest.mock("@/lib/csv", () => ({ downloadCSV: jest.fn() }));

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.releasePointerCapture = () => undefined;
});

beforeEach(() => jest.clearAllMocks());

interface Row {
  id: string;
  name: string;
  email: string | null;
  active: boolean;
}

const columns: SmartColumn<Row>[] = [
  { key: "name", header: "Nombre", hideable: false },
  { key: "email", header: "Email" },
  {
    key: "active",
    header: "Activo",
    cell: (r) => (r.active ? "sí" : "no"),
    csvValue: (r) => (r.active ? "Sí" : "No"),
  },
  { key: "secret", header: "Interna", defaultVisible: false, exportable: false },
];

const rows: Row[] = [
  { id: "1", name: "Juan", email: "j@x.com", active: true },
  { id: "2", name: "Ana", email: null, active: false },
];

describe("SmartTable (paginación del servidor)", () => {
  it("muestra la página recibida y pide la siguiente por offset, sin traer todo", async () => {
    const onPageChange = jest.fn();
    const onLimitChange = jest.fn();
    render(
      <SmartTable
        label="Gente"
        columns={columns}
        page={{ items: rows, total: 27, limit: 25, offset: 0 }}
        getRowId={(r) => r.id}
        onPageChange={onPageChange}
        onLimitChange={onLimitChange}
        renderActions={(r) => <button type="button">Editar {r.name}</button>}
      />,
    );
    const table = screen.getByRole("table", { name: "Gente" });
    expect(within(table).getByText("Juan")).toBeInTheDocument();
    expect(within(table).getByText("—")).toBeInTheDocument(); // email null
    expect(within(table).queryByText("Interna")).toBeNull(); // oculta por defecto
    expect(screen.getByText("Mostrando 1–2 de 27")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Página anterior" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Página siguiente" }));
    expect(onPageChange).toHaveBeenCalledWith(25);
    await userEvent.selectOptions(screen.getByLabelText(/filas por página/i), "50");
    expect(onLimitChange).toHaveBeenCalledWith(50);
    expect(screen.getByRole("button", { name: "Editar Ana" })).toBeInTheDocument();
  });

  it("en la última página: anterior vuelve por offset y siguiente se apaga", async () => {
    const onPageChange = jest.fn();
    render(
      <SmartTable
        label="Gente"
        columns={columns}
        page={{ items: rows, total: 27, limit: 25, offset: 25 }}
        getRowId={(r) => r.id}
        onPageChange={onPageChange}
      />,
    );
    expect(screen.getByText("Mostrando 26–27 de 27")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Página siguiente" })).toBeDisabled();
    expect(screen.queryByLabelText(/filas por página/i)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Página anterior" }));
    expect(onPageChange).toHaveBeenCalledWith(0);
  });

  it("columnas ocultables (nunca cero) y 'Exportar esta página' con lo visible", async () => {
    render(
      <SmartTable
        label="Gente"
        columns={columns}
        page={{ items: rows, total: 2, limit: 25, offset: 0 }}
        getRowId={(r) => r.id}
        onPageChange={jest.fn()}
        exportFilename="gente"
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /columnas/i }));
    const picker = screen.getByRole("group", { name: "Mostrar columnas" });
    await userEvent.click(within(picker).getByRole("checkbox", { name: "Email" }));
    await userEvent.click(within(picker).getByRole("checkbox", { name: "Interna" }));
    const table = screen.getByRole("table");
    expect(within(table).queryByText("j@x.com")).toBeNull();
    expect(within(table).getByRole("columnheader", { name: "Interna" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Exportar esta página" }));
    expect(downloadCSV).toHaveBeenCalledWith(
      "gente",
      ["Nombre", "Activo"],
      [
        ["Juan", "Sí"],
        ["Ana", "No"],
      ],
    );

    // Con una sola columna visible no se puede ocultar la última.
    await userEvent.click(within(picker).getByRole("checkbox", { name: "Activo" }));
    await userEvent.click(within(picker).getByRole("checkbox", { name: "Interna" }));
    expect(within(table).getAllByRole("columnheader")).toHaveLength(1);
  });

  it("estados: cargando, error y vacío; sin filas no se exporta", () => {
    const base = {
      label: "Gente",
      columns,
      getRowId: (r: Row) => r.id,
      onPageChange: jest.fn(),
    };
    const { rerender } = render(<SmartTable {...base} page={undefined} isLoading />);
    expect(screen.getByText("Cargando…")).toBeInTheDocument();
    rerender(<SmartTable {...base} page={undefined} error="Se cayó" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Se cayó");
    rerender(
      <SmartTable
        {...base}
        page={{ items: [], total: 0, limit: 25, offset: 0 }}
        emptyMessage="Nadie todavía"
      />,
    );
    expect(screen.getByText("Nadie todavía")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Exportar esta página" })).toBeDisabled();
    expect(screen.queryByText(/Mostrando/)).toBeNull();
  });
});

describe("PeriodFilter", () => {
  const NOW = new Date("2026-10-09T15:00:00Z");

  function Harness({ onChange }: { onChange: (v: PeriodValue) => void }) {
    const [value, setValue] = useState<PeriodValue>({ kind: "today" });
    return (
      <PeriodFilter
        value={value}
        now={NOW}
        onChange={(v) => {
          setValue(v);
          onChange(v);
        }}
      />
    );
  }

  it("presets y rango (ordenado, solo con fechas completas)", async () => {
    const onChange = jest.fn();
    render(<Harness onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Hoy" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Esta semana" }));
    expect(onChange).toHaveBeenLastCalledWith({ kind: "week" });
    await userEvent.click(screen.getByRole("button", { name: "Este mes" }));
    expect(onChange).toHaveBeenLastCalledWith({ kind: "month" });

    await userEvent.click(screen.getByRole("button", { name: "Rango" }));
    expect(onChange).toHaveBeenLastCalledWith({
      kind: "range",
      from: "2026-10-09",
      to: "2026-10-09",
    });
    const desde = screen.getByLabelText("Desde");
    await userEvent.clear(desde);
    expect(onChange).toHaveBeenCalledTimes(3); // vacío: no dispara
    await userEvent.type(desde, "2026-10-20");
    expect(onChange).toHaveBeenLastCalledWith({
      kind: "range",
      from: "2026-10-09",
      to: "2026-10-20",
    });
    await userEvent.clear(screen.getByLabelText("Hasta"));
    await userEvent.type(screen.getByLabelText("Hasta"), "2026-10-25");
    expect(onChange).toHaveBeenLastCalledWith({
      kind: "range",
      from: "2026-10-20",
      to: "2026-10-25",
    });
    await userEvent.click(screen.getByRole("button", { name: "Hoy" }));
    expect(screen.queryByLabelText("Desde")).toBeNull();
  });

  it("arranca mostrando el rango si el valor ya es un rango", () => {
    render(
      <PeriodFilter
        value={{ kind: "range", from: "2026-10-01", to: "2026-10-05" }}
        onChange={jest.fn()}
      />,
    );
    expect(screen.getByLabelText("Desde")).toHaveValue("2026-10-01");
  });
});

describe("MoneyInput (centavos enteros)", () => {
  function Harness({
    onChange,
    initial,
  }: {
    onChange: (c: number | null) => void;
    initial?: number | null;
  }) {
    const [value, setValue] = useState<number | null>(initial ?? null);
    return (
      <>
        <label htmlFor="m">Precio</label>
        <MoneyInput
          id="m"
          value={value}
          onChange={(c) => {
            setValue(c);
            onChange(c);
          }}
        />
        <button type="button" onClick={() => setValue(150000)}>
          externo
        </button>
        <button type="button" onClick={() => setValue(null)}>
          limpiar
        </button>
      </>
    );
  }

  it('"20.000" emite 2000000 y al salir se normaliza', async () => {
    const onChange = jest.fn();
    render(<Harness onChange={onChange} />);
    const input = screen.getByLabelText("Precio");
    await userEvent.type(input, "20.000");
    expect(onChange).toHaveBeenLastCalledWith(2000000);
    await userEvent.tab();
    expect(input).toHaveValue("20.000,00");
    expect(input).toHaveAttribute("inputmode", "decimal");
  });

  it('"abc" emite null (no 0) y el texto queda para corregir', async () => {
    const onChange = jest.fn();
    render(<Harness onChange={onChange} />);
    const input = screen.getByLabelText("Precio");
    await userEvent.type(input, "abc");
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(onChange).not.toHaveBeenCalledWith(0);
    await userEvent.tab();
    expect(input).toHaveValue("abc");
  });

  it("un valor de afuera reemplaza el texto; null limpia", async () => {
    render(<Harness onChange={jest.fn()} initial={2050} />);
    const input = screen.getByLabelText("Precio");
    expect(input).toHaveValue("20,50");
    await userEvent.click(screen.getByRole("button", { name: "externo" }));
    expect(input).toHaveValue("1.500,00");
    await userEvent.click(screen.getByRole("button", { name: "limpiar" }));
    expect(input).toHaveValue("");
  });
});

describe("Stepper", () => {
  it("marca hechos, actual y pendientes", () => {
    render(
      <Stepper
        current={1}
        steps={[
          { id: "a", label: "Negocio" },
          { id: "b", label: "Servicios" },
          { id: "c", label: "Equipo" },
        ]}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Pasos" })).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Negocio (hecho)");
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(items[2]).toHaveTextContent("3");
  });
});

describe("ConfirmDialog (nunca window.confirm)", () => {
  function Harness({ onConfirm }: { onConfirm: () => Promise<void> }) {
    const [open, setOpen] = useState(true);
    return (
      <>
        <p>{open ? "abierto" : "cerrado"}</p>
        <ConfirmDialog
          open={open}
          onOpenChange={setOpen}
          title="¿Borrar?"
          description="No se puede deshacer."
          confirmLabel="Borrar"
          variant="destructive"
          onConfirm={onConfirm}
        />
      </>
    );
  }

  it("confirma y cierra", async () => {
    const onConfirm = jest.fn(async () => undefined);
    render(<Harness onConfirm={onConfirm} />);
    const dialog = await screen.findByRole("dialog", { name: "¿Borrar?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    expect(onConfirm).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText("cerrado")).toBeInTheDocument());
  });

  it("si falla, muestra el error y no cierra; cancelar cierra", async () => {
    render(<Harness onConfirm={async () => Promise.reject(new Error("Está en uso"))} />);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Está en uso");
    expect(screen.getByText("abierto")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(screen.getByText("cerrado")).toBeInTheDocument());
  });

  it("un rechazo que no es Error usa el mensaje genérico", async () => {
    render(<Harness onConfirm={async () => Promise.reject("raro")} />);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/inesperado/);
  });
});
