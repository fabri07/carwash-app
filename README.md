# carwash.app

SaaS multi-tenant para lavaderos y centros de detailing.

Nace del sistema real que opera **Sola CleanCars** (Google Sheet + Apps Script), que pasa a ser
el tenant #1 y la fuente de las reglas de negocio.

## Estado

**Fase 1 — Spec.** Extracción de reglas desde el sistema actual. No se escribe modelo de datos
ni UI hasta que `docs/spec/02-reglas-negocio.md` esté congelado.

Ver [`docs/ROADMAP.md`](docs/ROADMAP.md) para el plan completo de 8 fases y el criterio de salida.

## Criterio de éxito de la primera entrega

Sola CleanCars opera **una semana completa** 100% en la app, con el Sheet apagado como sistema
operativo y conservado solo como backup histórico. El MVP no se declara terminado porque
"la app funciona".

## Estructura

```
docs/
  ROADMAP.md              plan de 8 fases (aprobado)
  spec/                   especificación extraída del sistema actual
    00-fuente-canonica.md   qué copia del Apps Script manda
    01-inventario-hojas.md  cada pestaña, columnas, dominios cerrados
    02-reglas-negocio.md    reglas en Given/When/Then, citadas al código
    03-perfil-datos.md      volúmenes, calidad, huecos
    04-modelo-datos.md      ERD y máquina de estados (Fase 3)
  adr/                    decisiones de arquitectura
  legacy/                 copias crudas del sistema actual, solo lectura
```

## Qué no está en este repositorio

`docs/spec/` y `docs/legacy/` son **local-only** y están en `.gitignore`.

Contienen la especificación extraída del sistema que opera hoy Sola CleanCars y una copia de su
código Apps Script. Ahí viven datos personales de clientes reales — patentes, teléfonos y nombres —
cuyo tratamiento cae bajo la Ley 25.326. No se publican.

Lo que sí es público es el plan: el roadmap de 8 fases, las convenciones de trabajo y, a partir de la
Fase 2, el código de la aplicación.
