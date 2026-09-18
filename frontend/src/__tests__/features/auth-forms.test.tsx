import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LoginForm } from "@/features/auth/LoginForm";
import { RegisterForm } from "@/features/auth/RegisterForm";

describe("RegisterForm (ADR-0010)", () => {
  it("no envía y muestra los errores del schema", async () => {
    const onSubmit = jest.fn();
    render(<RegisterForm onSubmit={onSubmit} />);
    await userEvent.click(screen.getByRole("button", { name: /crear cuenta/i }));
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.map((a) => a.textContent).join(" ")).toMatch(/email/i);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("cada campo se alcanza por su label", () => {
    render(<RegisterForm onSubmit={jest.fn()} />);
    expect(screen.getByLabelText(/nombre del negocio/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toHaveAttribute("type", "email");
    expect(screen.getByLabelText(/contraseña/i)).toHaveAttribute("type", "password");
  });

  it("el error del campo está anunciado para el lector de pantalla", async () => {
    render(<RegisterForm onSubmit={jest.fn()} />);
    await userEvent.type(screen.getByLabelText(/email/i), "no-es-email");
    await userEvent.click(screen.getByRole("button", { name: /crear cuenta/i }));
    const input = screen.getByLabelText(/email/i);
    const error = await screen.findByText("Email inválido");
    expect(error).toHaveAttribute("role", "alert");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", expect.stringContaining(error.id));
  });

  it("con datos válidos envía exactamente el contrato RegisterRequest", async () => {
    const onSubmit = jest.fn(async () => undefined);
    render(<RegisterForm onSubmit={onSubmit} />);
    await userEvent.type(screen.getByLabelText(/nombre del negocio/i), "  Sola CleanCars ");
    await userEvent.type(screen.getByLabelText(/email/i), "dueno@ejemplo.com");
    await userEvent.type(screen.getByLabelText(/contraseña/i), "12345678");
    await userEvent.click(screen.getByRole("button", { name: /crear cuenta/i }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        tenant: "Sola CleanCars",
        email: "dueno@ejemplo.com",
        password: "12345678",
      }),
    );
  });

  it("muestra el error del servidor que devuelve onSubmit", async () => {
    render(
      <RegisterForm onSubmit={async () => Promise.reject(new Error("Ya existe una cuenta"))} />,
    );
    await userEvent.type(screen.getByLabelText(/nombre del negocio/i), "X");
    await userEvent.type(screen.getByLabelText(/email/i), "a@b.com");
    await userEvent.type(screen.getByLabelText(/contraseña/i), "12345678");
    await userEvent.click(screen.getByRole("button", { name: /crear cuenta/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ya existe una cuenta");
  });
});

describe("LoginForm", () => {
  it("valida antes de enviar", async () => {
    const onSubmit = jest.fn();
    render(<LoginForm onSubmit={onSubmit} />);
    await userEvent.click(screen.getByRole("button", { name: /ingresar/i }));
    expect(await screen.findByText("Ingresá tu email")).toHaveAttribute("role", "alert");
    expect(screen.getByText("Ingresá tu contraseña")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("envía y muestra un error de servidor; un rechazo que no es Error usa el genérico", async () => {
    const onSubmit = jest
      .fn()
      .mockRejectedValueOnce(new Error("Email o contraseña incorrectos."))
      .mockRejectedValueOnce("raro");
    render(<LoginForm onSubmit={onSubmit} />);
    await userEvent.type(screen.getByLabelText(/email/i), "a@b.com");
    await userEvent.type(screen.getByLabelText(/contraseña/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /ingresar/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("incorrectos");
    await userEvent.click(screen.getByRole("button", { name: /ingresar/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/inesperado/));
    expect(onSubmit).toHaveBeenCalledWith({ email: "a@b.com", password: "x" });
  });

  it("el botón mide al menos 44px", () => {
    render(<LoginForm onSubmit={jest.fn()} />);
    expect(screen.getByRole("button", { name: /ingresar/i }).className).toMatch(/min-h-touch/);
  });
});
