# ADR-0010 · shadcn/ui + react-hook-form, no 20 componentes y zod a mano

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor escribió su sistema de componentes desde cero y valida formularios llamando a zod a mano.

**Los componentes.** `frontend/src/components/ui/` tiene **20 archivos** propios: `Badge`, `Button`,
`Card`, `EmptyState`, `Input`, `Modal`, `PeriodFilter`, `PinGateModal`, `Select`, `SmartTable`,
`StatCard`, `Table`, `TableSearch`, `Tabs`, `Toast`, `Tooltip`, `UploadSizeHint`, `VektorLogo`,
`VerticalIcons`, más el barrel `index.ts`. No hay shadcn/ui: `grep -n "shadcn\|class-variance-authority\|@radix-ui"
frontend/package.json` devuelve cero, y no existe `frontend/components.json`.

Eso significa que `Modal`, `Select`, `Tabs` y `Tooltip` —los cuatro casos donde la accesibilidad es
difícil: foco atrapado, cierre con `Escape`, `aria-expanded`, navegación con flechas, anuncio al lector de
pantalla— están implementados a mano, sin la base de Radix que resuelve exactamente eso.

**Los formularios.** No hay `react-hook-form` en `package.json`, ni un solo `useForm(` o `zodResolver` en
todo `src/`. Hay zod (`frontend/package.json:25`, `^3.23.8`) usado a mano: el schema se define en
`frontend/src/validation/auth.ts:3-5` y después se invoca con `safeParse` desde el componente —
**tres veces en el mismo formulario**: `frontend/src/features/auth/LoginForm.tsx:62`, `:97` y `:128` (esta
última solo para decidir si el botón está habilitado). Lo mismo, más grande, en
`frontend/src/validation/accessRequest.ts:53,69,76-89` invocado desde `:173`.

Por qué no se hereda: cada formulario reimplementa el mismo estado —valores, errores por campo, `touched`,
`isSubmitting`, cuándo mostrar el error— y lo reimplementa distinto. El `LoginForm` de Véktor llama al
mismo `safeParse` en tres momentos con tres propósitos; el próximo formulario lo va a hacer en dos, o en
cuatro. Y sin `react-hook-form`, cada tecleo re-renderiza el formulario entero, que en un formulario de
carga de lavado en un celular gama baja se nota.

El costo compuesto es peor: con 20 componentes propios, cada mejora de accesibilidad hay que hacerla 20
veces. El dominio de este proyecto se usa **de pie, con el celular, con las manos mojadas**. El teclado
correcto en un input numérico y un target táctil de 44px no son detalles.

## Decisión

1. **shadcn/ui como base**: Tailwind + Radix primitives + `class-variance-authority`, con
   `frontend/components.json` versionado. Los componentes se vendorizan en `src/components/ui/` (así los
   trae shadcn) y **se pueden editar** — no es una dependencia opaca, es código propio con buen punto de
   partida.
2. **Solo los componentes que la Fase 2 usa**: `button`, `input`, `label`, `form`, `card`, `sonner`
   (toast), `dropdown-menu`, `sheet` (el menú lateral en móvil). Traer los 40 "por si acaso" es la misma
   deuda con otro origen.
3. **`react-hook-form` + `@hookform/resolvers/zod`** para todo formulario. Un schema zod por formulario en
   `src/validation/`, y el tipo del formulario sale de `z.infer<typeof schema>` — **el schema es la única
   fuente**, no hay una interfaz TS paralela que mantener.
4. **Prohibido llamar a `.safeParse()` desde un componente.** Si hace falta validar, es un schema y un
   resolver.
5. **Los tres componentes propios que sí se portan** son los que no tienen equivalente en shadcn y ya están
   probados en producción: la mecánica del `Toast` (se reemplaza por `sonner`, pero el `toastStore` de
   Véktor define el contrato de tipos y duraciones), el `EmptyState` y el `AuthHydrationBoundary`. El resto
   se descarta.
6. **Accesibilidad como piso, no como mejora**: todo control de formulario tiene `<label htmlFor>`, todo
   error se anuncia con `aria-describedby` + `role="alert"`, y los tests usan `getByLabelText` — que es un
   test de accesibilidad disfrazado de test funcional: si el label no está atado al input, no compila el
   test.

## Consecuencias

- **Gana:** el foco, el `Escape`, el `aria-*` y la navegación por teclado del modal y del select vienen de
  Radix, que los resolvió para todos. Se dejan de reimplementar mal.
- **Gana:** un formulario nuevo es un schema y un `useForm`. El estado de errores, `touched` e
  `isSubmitting` deja de ser código que se escribe.
- **Gana:** menos re-renders por tecla (RHF trabaja con refs no controlados), que es exactamente lo que
  importa en el celular del playón.
- **Cuesta:** dependencias nuevas (`react-hook-form`, `@hookform/resolvers`, `@radix-ui/*`,
  `class-variance-authority`, `tailwind-merge`). Es un intercambio deliberado: menos código propio, más
  superficie de terceros. Radix es la parte que menos quiero escribir yo.
- **Cuesta:** shadcn no es una librería versionada — actualizar un componente es volver a correr el `add` y
  resolver el diff a mano. A cambio, nunca hay una versión que rompa la app entera.
- **Recordar:** `sonner` reemplaza al `Toast` de Véktor, pero el contrato (tipos `success|error|info`,
  duración, cola) sale del `toastStore` portado. No inventarlo de nuevo.

## Cómo se verifica

```bash
test -f frontend/components.json                                       # shadcn inicializado
cd frontend && node -e '
  const d = {...require("./package.json").dependencies};
  for (const p of ["react-hook-form","@hookform/resolvers","class-variance-authority","tailwind-merge","zod"])
    if (!d[p]) { console.error("falta", p); process.exit(1); }'
```

`frontend/src/__tests__/meta/formularios.test.ts` — la guarda que impide volver al patrón de Véktor:

```ts
it("ningún componente llama a .safeParse() a mano", () => {
  const culpables = glob.sync("src/**/*.tsx")
    .filter((f) => fs.readFileSync(f, "utf8").includes(".safeParse("));
  expect(culpables).toEqual([]);   // LoginForm.tsx:62,:97,:128 de Véktor caen acá
});

it("todo formulario usa useForm con zodResolver", () => {
  for (const f of glob.sync("src/**/*Form.tsx")) {
    const src = fs.readFileSync(f, "utf8");
    expect(src).toMatch(/useForm/);
    expect(src).toMatch(/zodResolver/);
  }
});
```

`frontend/src/features/auth/__tests__/RegisterForm.test.tsx` — comportamiento y accesibilidad en el mismo
test:

```tsx
it("no envía y muestra los errores del schema", async () => {
  const onSubmit = jest.fn();
  render(<RegisterForm onSubmit={onSubmit} />);
  await userEvent.click(screen.getByRole("button", { name: /crear cuenta/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/email/i);
  expect(onSubmit).not.toHaveBeenCalled();
});

it("cada campo se alcanza por su label", () => {
  render(<RegisterForm onSubmit={jest.fn()} />);
  // si el <label htmlFor> no está atado al input, getByLabelText tira y el test falla.
  expect(screen.getByLabelText(/email/i)).toHaveAttribute("type", "email");
  expect(screen.getByLabelText(/contraseña/i)).toHaveAttribute("type", "password");
});

it("el error del campo está anunciado para el lector de pantalla", async () => {
  /* … expect(input).toHaveAttribute("aria-describedby", expect.stringContaining(errorId)) */
});
```
