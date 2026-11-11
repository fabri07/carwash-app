import { act, renderHook } from "@testing-library/react";
import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from "axios";

import { useCatalogEditor, useStepBackOnEmptyPage } from "@/hooks/useCatalogEditor";
import { FormSubmitError } from "@/lib/errors";
import { useToastStore } from "@/stores/toastStore";

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, code: string) {
  return new AxiosError("x", "ERR", config, null, {
    status,
    data: { detail: { code, message: "x" } },
    statusText: "",
    headers: {},
    config,
  });
}

beforeEach(() => useToastStore.setState({ toasts: [] }));
const last = () => useToastStore.getState().toasts.at(-1)?.message;

describe("useCatalogEditor", () => {
  function setup(feminine: boolean, create = jest.fn(), update = jest.fn()) {
    const refresh = jest.fn().mockResolvedValue(undefined);
    const hook = renderHook(() =>
      useCatalogEditor<{ id: string }, string>({
        singular: feminine ? "franja" : "puesto",
        feminine,
        create,
        update,
        refresh,
      }),
    );
    return { hook, refresh, create, update };
  }

  it("alta y edición con el género del sustantivo", async () => {
    const { hook, create, update, refresh } = setup(true);
    await act(() => hook.result.current.save("v", undefined, "k1"));
    expect(create).toHaveBeenCalledWith("v", "k1");
    expect(last()).toBe("Franja creada.");
    await act(() => hook.result.current.save("v", { id: "1" }));
    expect(update).toHaveBeenCalledWith({ id: "1" }, "v");
    expect(last()).toBe("Franja actualizada.");
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("el replay es 'ya estaba hecho'; otro error va al formulario", async () => {
    const create = jest.fn().mockRejectedValue(apiError(409, "DUPLICATE_IDEMPOTENT"));
    const { hook } = setup(false, create);
    await act(() => hook.result.current.save("v"));
    expect(last()).toBe("Ese puesto ya se había creado. Revisá en la lista cómo quedó.");
    create.mockRejectedValue(apiError(409, "NAME_TAKEN"));
    await expect(hook.result.current.save("v")).rejects.toBeInstanceOf(FormSubmitError);
  });

  it("submitFor usa la clave del diálogo de alta", async () => {
    const { hook, create } = setup(false);
    await act(() => hook.result.current.submitFor({ kind: "create", idempotencyKey: "kk" })("v"));
    expect(create).toHaveBeenCalledWith("v", "kk");
  });
});

describe("useStepBackOnEmptyPage", () => {
  it("vuelve a la última página con datos", () => {
    const goTo = jest.fn();
    renderHook(() => useStepBackOnEmptyPage({ items: [], total: 51 }, 75, 25, goTo));
    expect(goTo).toHaveBeenCalledWith(50);
  });

  it("no se mueve si la página tiene datos o es la primera", () => {
    const goTo = jest.fn();
    renderHook(() => useStepBackOnEmptyPage({ items: [1], total: 51 }, 50, 25, goTo));
    renderHook(() => useStepBackOnEmptyPage({ items: [], total: 0 }, 0, 25, goTo));
    expect(goTo).not.toHaveBeenCalled();
  });
});
