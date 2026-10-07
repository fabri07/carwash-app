# Glosario del rubro — lavaderos y talleres de detailing

**Para qué sirve:** que la app (y quien la programa) hable como el rubro. Alimenta tres cosas de F4: las
**plantillas sugeridas** del wizard, las **categorías** propuestas y la **búsqueda tolerante** (sin tildes,
en castellano o en inglés). **No es un catálogo cerrado:** cada negocio define sus servicios, nombres y
precios; ningún término de acá se crea solo.

**Alcance:** español rioplatense primero, con el término en inglés que el rubro usa tal cual. Las
variantes de otros países se suman en una columna nueva, sin cambiar las existentes.

**Estado:** v2, 2026-10-07. Borrador para validar con el dueño. No lleva precios ni duraciones: dependen
de cada negocio y del tamaño del vehículo.

Columnas: **Término** (como lo escribe un dueño argentino) · **Tipo** · **Inglés** (como aparece en el
rubro) · **También lo buscan como** (sinónimos para la búsqueda) · **Qué es**.

---

## 0. Cómo se usa este glosario

El cambio más importante respecto de la v1: **no todos los términos del rubro son servicios vendibles.**
Si el wizard sugiere plantillas leyendo las tablas enteras, termina ofreciendo "Swirls" o "Clay bar" como
ítems de catálogo. Cada fila lleva ahora un **Tipo** que define qué puede hacer la app con ella.

| Tipo | Qué es | Qué hace la app con él |
|---|---|---|
| `servicio` | Trabajo vendible por sí solo. | Candidato a plantilla de servicio en el wizard. |
| `adicional` | Se vende sumado a un servicio, rara vez solo. | Candidato a adicional / upsell. |
| `tecnica` | Cómo se hace el trabajo, no qué se vende. | Solo búsqueda y descripción. Nunca se sugiere como ítem. |
| `defecto` | Problema que el cliente quiere resolver. | Entrada de búsqueda: mapea a los servicios que lo resuelven. |
| `producto` | Se vende al mostrador. | Plantilla de producto (F9). |
| `insumo` | Se consume al trabajar; no se vende. | Stock y costo, nunca catálogo. |
| `modificador` | Cambia precio o duración de un servicio. | Recargo, variante o regla de precio. |
| `entidad` | Concepto del modelo de datos. | Nombre canónico en código y en la UI. |

Dos reglas que se desprenden:

- **Un `defecto` nunca es un servicio, pero sí es una búsqueda legítima:** quien escribe "sacar rayas"
  tiene que llegar a *Corrección de pintura*. La columna de sinónimos no alcanza; hace falta un mapeo
  defecto → servicios (sección 13).
- **Una `tecnica` se usa para describir un servicio, no para venderlo.** "Lavado de dos baldes" es cómo
  lavás, no un ítem distinto de "Lavado exterior".

---

## 1. Lavado

| Término | Tipo | Inglés | También lo buscan como | Qué es |
|---|---|---|---|---|
| Lavado exterior | servicio | Exterior wash | lavado simple, lavado básico, lavado por fuera | Carrocería, vidrios y llantas. |
| Lavado rápido | servicio | Express wash | lavado express, lavadito, lavado económico | Exterior acotado, pensado por tiempo de ciclo corto. |
| Lavado completo | servicio | Full wash | lavado full, lavado interior y exterior | Exterior más aspirado y limpieza de interior básica. |
| Lavado premium | servicio | Premium wash | lavado con cera, lavado especial | Lavado completo con un plus (cera rápida, acondicionador de plásticos). Cada negocio define el plus. |
| Lavado a mano | tecnica | Hand wash | a mano, lavado manual | Sin túnel ni cepillos mecánicos. Argumento de venta, no un ítem aparte. |
| Prelavado / espuma activa | tecnica | Snow foam, pre-wash | espuma, prewash, foam | Espuma que afloja la suciedad antes del contacto, para no rayar. |
| Lavado de dos baldes | tecnica | Two bucket method | dos baldes | Técnica de lavado a mano que separa el agua limpia de la de enjuague. |
| Hidrolavado | tecnica | Pressure washing | hidrolavadora, agua a presión, karcher | Enjuague y prelavado con equipo de presión. |
| Lavado sin agua | servicio | Waterless wash | lavado ecológico, lavado en seco | Con producto que encapsula la suciedad; sirve a domicilio. |
| Lavado de chasis | adicional | Undercarriage wash | lavado de bajos, chasis, bajos | Parte de abajo del auto, barro y sal. |
| Lavado de motor | adicional | Engine bay cleaning | motor, limpieza de motor, engine | Vano motor desengrasado y acondicionado. |
| Lavado de llantas | adicional | Wheel cleaning | llantas, rines, ruedas | Llantas y pasaruedas con descontaminante de hierro o desengrasante. |
| Desmontaje de ruedas | adicional | Wheels off | ruedas afuera, sacar las ruedas | Se quitan las ruedas para limpiar interior de llanta, pinzas y suspensión. |
| Limpieza de pasaruedas | adicional | Wheel well cleaning | guardabarros, pasarruedas, paso de rueda | Desengrase del plástico interno del guardabarros. |
| Limpieza de pinzas de freno | adicional | Caliper cleaning | calipers, pinzas, cálipers | Pinzas y discos, generalmente con ruedas desmontadas. |
| Remoción de insectos | adicional | Bug removal | insectos, bichos, frente, mosquitos | Frente, paragolpes y espejos con producto específico. |
| Remoción de alquitrán | adicional | Tar removal | brea, alquitrán, chapopote | Puntos negros de asfalto en laterales y faldones. |
| Acondicionado de cubiertas | adicional | Tire dressing | brillo de cubiertas, renovador de neumáticos, silicona de ruedas | Producto que oscurece y protege la goma. |
| Abrillantado de llantas | adicional | Wheel polish | brillo de llantas, pulido de llantas | Brillo sobre la llanta ya descontaminada. |
| Secado | tecnica | Drying | secado con microfibra, secado con aire, soplado | Con toalla de microfibra o soplador, para evitar marcas de agua. |
| Limpieza de baúl | adicional | Trunk cleaning | baul, maletero, baúl | Aspirado y limpieza del compartimiento de carga. |
| Lavado de capota de lona | servicio | Convertible top cleaning | capota, lona, descapotable | Limpieza y reimpermeabilizado de capota textil. |

## 2. Interior

| Término | Tipo | Inglés | También lo buscan como | Qué es |
|---|---|---|---|---|
| Aspirado | servicio | Vacuum | aspirada, aspirado completo | Pisos, alfombras y asientos. |
| Limpieza de interior | servicio | Interior detailing | detailing interior, interior, interior completo | Plásticos, paneles, consola, vidrios por dentro, rejillas. |
| Limpieza de tapizados | servicio | Upholstery cleaning | tapizados, tapicería, limpieza de asientos, shampoo de asientos | Tela con inyección y extracción, o espuma seca. |
| Inyección y extracción | tecnica | Hot water extraction | extractora, inyección extracción, lavado con extractora | Se inyecta solución y se extrae con la suciedad. |
| Limpieza a vapor | tecnica | Steam cleaning | vapor, vaporeta | Calor y poca humedad; sirve en rejillas, costuras y zonas sensibles. |
| Limpieza de cueros | servicio | Leather cleaning | cuero, asientos de cuero, cuerina | Limpieza con producto de pH neutro. |
| Hidratación de cueros | adicional | Leather conditioning | acondicionado de cuero, nutrición de cuero | Producto que evita que el cuero se reseque y se agriete. |
| Limpieza de alfombras | adicional | Carpet shampoo | alfombras, moquetas, alfombritas | Lavado profundo con extracción. |
| Limpieza de techo | adicional | Headliner cleaning | techo interior, cielorraso, techo de tela | Tela del techo, con poca humedad para que no se despegue. |
| Limpieza de cinturones | adicional | Seat belt cleaning | cinturones, correas | Extracción completa del cinturón y lavado de la cinta. |
| Limpieza de rejillas y conductos | adicional | Vent cleaning | rejillas, salidas de aire, ventilación | Polvo de rejillas y conductos de aire. |
| Limpieza de vidrios interiores | adicional | Interior glass | vidrios por dentro, parabrisas interior, empañado | Película grasa del lado interno del vidrio. |
| Acondicionado de plásticos | adicional | Interior dressing | silicona de tablero, renovador de plásticos, tablero | Protege y empareja el brillo de tablero y paneles. |
| Ozonizado | adicional | Ozone treatment | ozono, desodorizado, sacar olor | Elimina olores (cigarrillo, humedad) con generador de ozono. |
| Sanitizado | adicional | Sanitizing | desinfección, sanitización | Desinfección del habitáculo y de los conductos del aire. |
| Pelos de mascota | adicional | Pet hair removal | pelos de perro, mascotas, pelos | Extracción de pelos incrustados en la tela. |
| Remoción de manchas puntuales | adicional | Spot treatment | manchas, mancha de café, quitamanchas | Tratamiento localizado antes o después del lavado general. |
| Impermeabilizado de tapizados | adicional | Fabric protection | antimanchas, impermeabilizante de tela, scotchgard | Protección que repele líquidos sobre tela limpia. |
| Cerámico de interior | adicional | Interior coating | cerámico de tapizados, coating interior | Recubrimiento protector sobre cuero, tela o plásticos. |

## 3. Detailing exterior y corrección de pintura

| Término | Tipo | Inglés | También lo buscan como | Qué es |
|---|---|---|---|---|
| Descontaminado | servicio | Decontamination | descontaminación, deco | Saca lo que el lavado no saca: hierro, alquitrán, resina. |
| Descontaminado químico | tecnica | Iron removal, fallout remover | removedor de hierro, iron, antiférrico | Producto que disuelve partículas de hierro (se pone violeta). |
| Clay bar / arcilla | tecnica | Clay bar | arcilla, clay, barra de arcilla | Descontaminado mecánico; deja la pintura lisa antes de pulir o proteger. |
| Pulido | servicio | Polish | pulida, abrillantado, brillado, lustrado | Saca marcas finas y devuelve el brillo. |
| Corrección de pintura | servicio | Paint correction | corrección, corrección en una etapa / dos etapas | Pulido en una o más etapas (corte y terminación) para sacar rayas y swirls. |
| Corte | tecnica | Compounding, cut | compuesto, pulido de corte | Primera etapa de la corrección, más abrasiva. |
| Terminación | tecnica | Finishing | acabado, refinado | Última etapa: brillo y profundidad sin hologramas. |
| Lijado en húmedo | tecnica | Wetsanding | lijado, lija al agua | Lija fina sobre defectos profundos; previo al corte. Alto riesgo. |
| Medición de espesor | tecnica | Paint depth gauge | medidor de pintura, micrones, espesor | Medir la pintura antes de corregir, para no quemarla. |
| Restauración de ópticas | servicio | Headlight restoration | faros, ópticas, pulido de faros, faros amarillos | Saca lo amarillo y opaco del policarbonato y lo protege. |
| Restauración de plásticos exteriores | servicio | Trim restoration | plásticos, molduras, renovador de plásticos, burletes | Devuelve el negro a plásticos y burletes. |
| Abrillantado de cromos y escapes | adicional | Metal polishing | cromos, escape, acero, aluminio pulido | Pulido de partes metálicas y colas de escape. |
| Tratamiento de vidrios | adicional | Glass coating | repelente de lluvia, hidrofóbico de vidrios, antilluvia | Producto que hace correr el agua en el parabrisas. |
| Remoción de calcáreo | adicional | Water spot removal | manchas de agua, sarro, calcáreo, agua dura | Saca las marcas de minerales del agua secada al sol. |
| Remoción de overspray | adicional | Overspray removal | pintura salpicada, overspray, salpicaduras de obra | Partículas de pintura ajena adheridas a la carrocería. |
| Retoque de pintura | adicional | Touch up | retoque, pincel, tapar rayón | Relleno puntual de rayones que llegan a la base. |

## 4. Protección

| Término | Tipo | Inglés | También lo buscan como | Qué es |
|---|---|---|---|---|
| Encerado | servicio | Wax | cera, cera de carnauba, encerado a mano | Protección y brillo de corta duración. |
| Sellador | servicio | Sealant | sellador sintético, paint sealant | Protección sintética, dura más que la cera. |
| Tratamiento cerámico | servicio | Ceramic coating | cerámico, ceramico, coating, nano cerámico | Capa dura que se adhiere a la pintura; necesita pintura corregida y tiempo de curado. |
| Grafeno | servicio | Graphene coating | grafeno, coating de grafeno | Variante del cerámico con grafeno. |
| Cerámico de llantas | adicional | Wheel coating | cerámico de ruedas, coating de llantas | Recubrimiento sobre llanta descontaminada; facilita el lavado posterior. |
| Cerámico de vidrios | adicional | Glass coating | coating de parabrisas, cerámico de vidrios | Versión durable del repelente de lluvia. |
| Mantenimiento del cerámico | servicio | Coating maintenance | mantenimiento, refuerzo, top coat, service de cerámico | Lavado y reaplicación periódica para que el cerámico rinda. |
| Capa sacrificial | adicional | Topper, sacrificial layer | topper, capa de sacrificio | Protección liviana sobre el cerámico, que se renueva seguido. |
| Film de protección de pintura | servicio | Paint protection film (PPF) | ppf, film, film protector, lámina protectora, antigravilla | Lámina transparente contra piedras y rayas; frente completo o auto entero. |
| Polarizado | servicio | Window tint | polarizado, láminas de vidrios, tint | Lámina oscura para vidrios. |
| Vinilo / ploteo | servicio | Vinyl wrap | ploteo, wrap, cambio de color | Lámina de color que cubre la pintura original. |
| Hidrofóbico | tecnica | Hydrophobic | efecto perla, repelente | Propiedad de una superficie que repele el agua. |
| Curado | modificador | Curing | tiempo de curado, fragua | Tiempo que el cerámico necesita sin agua; puede dejar el auto en el taller más de un día. |
| Garantía de recubrimiento | entidad | Coating warranty | garantía, años de garantía | Compromiso por duración, condicionado a mantenimientos periódicos. |
| Inspección de garantía | servicio | Warranty inspection | revisión de garantía, control anual | Visita periódica que mantiene vigente la garantía del cerámico. |

## 5. Defectos y problemas (lo que el cliente quiere sacar)

Ninguno es vendible. Sirven como entrada de búsqueda y como motivo registrado en la orden de trabajo.

| Término | Tipo | Inglés | También lo buscan como | Lo resuelve |
|---|---|---|---|---|
| Swirls / telarañas | defecto | Swirl marks | telarañas, marcas circulares, micro rayas, rayitas | Corrección de pintura, Pulido |
| Hologramas | defecto | Holograms | buffer trails, marcas de pulidora | Corrección de pintura (terminación) |
| Rayón profundo | defecto | Deep scratch | rayón, rayon, raya profunda, arañazo | Retoque de pintura, Lijado en húmedo |
| Oxidación de pintura | defecto | Oxidation | pintura opaca, despintado, quemado por sol | Corrección de pintura |
| Cáscara de naranja | defecto | Orange peel | piel de naranja, textura de pintura | Lijado en húmedo + corrección |
| Manchas de agua | defecto | Water spots | calcáreo, sarro, marcas de agua, agua dura | Remoción de calcáreo |
| Excremento de pájaro | defecto | Bird dropping etching | caca de pájaro, marca de pájaro | Corrección de pintura, Descontaminado |
| Resina de árbol | defecto | Tree sap | savia, resina, pegote de árbol | Descontaminado |
| Contaminación ferrosa | defecto | Iron fallout | puntitos naranjas, óxido en la pintura | Descontaminado químico |
| Faros amarillos | defecto | Yellowed headlights | faros opacos, ópticas amarillas | Restauración de ópticas |
| Plásticos grises | defecto | Faded trim | plásticos descoloridos, molduras blancas | Restauración de plásticos exteriores |
| Olor persistente | defecto | Odor | olor a humedad, olor a cigarrillo, huele feo | Ozonizado, Limpieza de tapizados |
| Moho / humedad | defecto | Mold | hongos, humedad en los asientos | Limpieza de tapizados, Sanitizado |
| Barro de obra | defecto | Heavy soil | muy sucio, embarrado, de obra | Recargo por estado + Lavado completo |

## 6. Productos que se venden (F9)

Son ejemplos para la plantilla de productos; cada negocio carga los suyos.

| Término | Tipo | Inglés | También lo buscan como |
|---|---|---|---|
| Aromatizante | producto | Air freshener | perfume, aromatizador, olorcito, arbolito |
| Shampoo para autos | producto | Car shampoo | shampoo neutro, champú, shampoo siliconado |
| Cera en spray | producto | Spray wax | cera rápida, quick wax |
| Quick detailer | producto | Quick detailer | detallador rápido, QD |
| Paño de microfibra | producto | Microfiber towel | microfibra, franela, trapo |
| Toalla de secado | producto | Drying towel | toalla de secado, paño de secado |
| Renovador de cubiertas | producto | Tire shine | brillo de cubiertas, silicona de ruedas |
| Limpiador de interiores | producto | Interior cleaner | APC, multiuso, limpiatodo |
| Kit de mantenimiento | producto | Maintenance kit | kit de lavado, kit cerámico |
| Guante de microfibra | producto | Wash mitt | manopla, guante de lavado |
| Gift card | producto | Gift card | tarjeta de regalo, voucher, regalo |

## 7. Insumos y herramientas (no se venden)

Consumo y costo, nunca catálogo. Se vinculan al servicio para calcular rendimiento.

| Término | Tipo | Inglés | También lo buscan como | Qué es |
|---|---|---|---|---|
| Antiférrico | insumo | Iron remover | removedor de hierro, descontaminante | Disuelve partículas de hierro. |
| Desengrasante | insumo | Degreaser | desengrasante, degreaser | Motor, guardabarros, llantas. |
| APC | insumo | All purpose cleaner | multiuso, limpiador multipropósito | Limpiador de uso general diluible. |
| Compuesto de corte | insumo | Compound | pulimento de corte, compuesto | Abrasivo de primera etapa. |
| Pulimento de terminación | insumo | Polish | pulimento, refinador | Abrasivo fino de última etapa. |
| Boina | insumo | Pad | pad, boina de lana, boina de espuma | Disco que monta la pulidora. |
| Pulidora orbital | insumo | DA polisher | orbital, DA, pulidora | Herramienta de corrección de uso general. |
| Pulidora rotativa | insumo | Rotary polisher | rotativa | Mayor corte, mayor riesgo de quemar pintura. |
| Extractora | insumo | Extractor | inyectora extractora, karcher de tapizados | Equipo de inyección y extracción. |
| Generador de ozono | insumo | Ozone generator | ozonizador, máquina de ozono | Equipo para desodorizado. |
| Hidrolavadora | insumo | Pressure washer | karcher, máquina de presión | Equipo de enjuague a presión. |
| Soplador | insumo | Air blower | sopladora, aire a presión | Secado de hendiduras y vano motor. |
| Balde con grid | insumo | Grit guard bucket | balde con rejilla, grit guard | Retiene la suciedad en el fondo del balde. |

## 8. Vehículos y segmentación

El tamaño es el eje de precio principal del rubro. Nombrarlo bien evita el error de poner el precio en el
servicio en vez de en la combinación servicio × segmento.

| Término | Tipo | Inglés | También lo buscan como | Qué es |
|---|---|---|---|---|
| Segmento de vehículo | entidad | Vehicle size tier | tamaño, categoría, porte | Agrupación que define precio y duración. |
| Auto | modificador | Sedan, hatchback | sedán, chico, compacto, auto común | Segmento base de la mayoría de las listas. |
| SUV | modificador | SUV, crossover | camioneta chica, crossover, suv | Mayor superficie y altura. |
| Utilitario | modificador | Van, cargo van | furgón, furgoneta, utilitario | Volumen de carga; interior distinto. |
| Pick-up | modificador | Pickup truck | camioneta, pick up, pickup, doble cabina | Caja abierta, mayor superficie. |
| 4x4 grande | modificador | Full-size SUV | camionetón, 4x4, todoterreno | Escalón superior de SUV. |
| Moto | modificador | Motorcycle | moto, motos | Proceso y tiempos propios. |
| Vehículo de flota | entidad | Fleet vehicle | flota, empresa, cuenta corriente | Pertenece a una cuenta, no a una persona. |
| Patente | entidad | License plate | patente, dominio, chapa | Identidad natural del vehículo. PII fuerte: va al scrubbing de Sentry. |
| Ficha del vehículo | entidad | Vehicle profile | historial del auto, ficha | Historial de trabajos, estado de protección, notas. |

## 9. Modificadores de precio y duración

| Término | Tipo | Inglés | También lo buscan como | Qué es |
|---|---|---|---|---|
| Recargo por tamaño | modificador | Size surcharge | recargo por camioneta, diferencia por tamaño | Precio distinto del mismo servicio por segmento. |
| Recargo por estado | modificador | Condition surcharge | muy sucio, recargo por suciedad, extra por barro | Se cobra cuando el vehículo supera el estado esperado. |
| Recargo por pelo de mascota | modificador | Pet hair surcharge | extra por pelos | Trabajo adicional no previsto en el servicio base. |
| Recargo por color | modificador | Paint color factor | negro, color oscuro | El negro muestra más defectos; algunos talleres lo cobran aparte. |
| Tiempo de curado | modificador | Cure time | curado, fragua | Ocupa el box sin trabajo activo. |
| Servicio a cotizar | modificador | Quote-only | a cotizar, presupuestar, previa revisión | No tiene precio de lista; requiere ver el vehículo. |

## 10. Comercial, abonos y fidelización

| Término | Tipo | Inglés | Qué es en la app |
|---|---|---|---|
| Abono / suscripción | entidad | Subscription plan | Pago periódico que da derecho a una cantidad de servicios. |
| Crédito de lavado | entidad | Service credit | Unidad emitida por el abono, consumida por un job, con fecha de vencimiento. |
| Paquete / combo | entidad | Package, bundle | Conjunto de servicios con precio propio. |
| Adicional / upsell | entidad | Add-on | Ítem que se suma a un job ya cargado. |
| Cupón | entidad | Coupon | Descuento con condición y vencimiento. |
| Referido | entidad | Referral | Cliente traído por otro cliente, con beneficio asociado. |
| Reseña | entidad | Review | Calificación posterior a la entrega. |
| Cliente recurrente | entidad | Returning customer | Cliente con más de un job cerrado. Métrica central del negocio. |
| Frecuencia de retorno | entidad | Visit frequency | Días promedio entre jobs de un mismo vehículo. |
| Recordatorio de mantenimiento | entidad | Maintenance reminder | Aviso programado según el último servicio y su duración esperada. |

## 11. Operación

| Término | Tipo | Inglés | Qué es en la app |
|---|---|---|---|
| Turno | entidad | Booking, appointment | `bookings`. |
| Orden de trabajo / job | entidad | Work order, job | El trabajo en sí. Un turno puede no tener job (no-show) y un job puede no tener turno (walk-in). |
| Estadía | entidad | Vehicle stay | Tiempo total del vehículo en el taller. En detailing puede abarcar varios días y varios jobs. |
| Seña | entidad | Deposit | Pago adelantado que confirma el turno. |
| Puesto / box | entidad | Bay | `resources`: dónde se lava o se trabaja el auto. Es la unidad de capacidad, no el operario. |
| Operario / detailer | entidad | Detailer, technician | Quien ejecuta el trabajo. Puede atender más de un box. |
| Capacidad simultánea | entidad | Concurrent capacity | Cuántos vehículos puede tener el local a la vez. |
| Buffer | entidad | Buffer time | Tiempo entre turnos para secado, orden o curado. |
| Sobreturno | entidad | Overbooking | Turno aceptado por encima de la capacidad nominal. |
| Lista de espera | entidad | Waitlist | Clientes a avisar si se libera un turno. |
| Reprogramación | entidad | Reschedule | Cambio de fecha del turno, conservando su historia. |
| No-show | entidad | No-show | El cliente no se presentó. Distinto de cancelación. |
| Cancelación tardía | entidad | Late cancellation | Cancelación dentro de la ventana que afecta la seña. |
| Cierre por lluvia | entidad | Weather closure | Excepción de calendario que libera turnos del día. |
| Ingreso / recepción | entidad | Check-in | `JOB_RECEIVED`: el auto llega al local. |
| Inspección / estado del auto | entidad | Vehicle inspection | `job_inspections`: daños previos, objetos de valor. Opcional. |
| Objetos de valor | entidad | Valuables | Se registran en la inspección; deslinda responsabilidad. |
| Llaves | entidad | Key handling | Quién tiene la llave durante la estadía. |
| Foto de antes y después | entidad | Before/after photo | Evidencia del trabajo. Insumo de marketing y de reclamo. |
| Control de calidad | entidad | Quality check | Revisión previa a declarar el job terminado. |
| Retrabajo | entidad | Rework | Se rehace sin cobrar por falla propia. Métrica de calidad, no un job nuevo. |
| Retiro | entidad | Pick-up | `JOB_PICKED_UP`: el cliente se lleva el auto. |
| Cotización / presupuesto | entidad | Quote | `quotes`: servicios "a cotizar", típicos del detailing. |
| Al paso / sin turno | entidad | Walk-in | Job sin turno. |
| Tiempo de ciclo | entidad | Cycle time | Desde el ingreso hasta el retiro. Distinto de la duración del servicio. |
| Ocupación de box | entidad | Bay utilization | Porcentaje de horas de box efectivamente trabajadas. |
| Lavado a domicilio | entidad | Mobile detailing | Fuera de alcance por ahora: no hay puesto propio. |

*Patente* está definida en la sección 8 (es PII fuerte: va al scrubbing de Sentry).

## 12. Cómo lo pide el cliente

Lenguaje real de WhatsApp y del mostrador. No son términos del rubro: son entradas de búsqueda y de
clasificación automática del pedido.

| Lo que escribe el cliente | A qué apunta |
|---|---|
| "un lavadito", "una lavadita" | Lavado rápido |
| "el completo", "hacele el completo" | Lavado completo |
| "dejalo como nuevo", "ponelo impecable" | Detailing completo / a cotizar |
| "sacarle las rayas" | Corrección de pintura |
| "está todo opaco" | Pulido / Corrección |
| "una pulida" | Pulido (suele significar corrección) |
| "lo quiero para vender" | Lavado completo + Pulido + Interior |
| "le hago el cerámico" | Tratamiento cerámico |
| "viene de la obra", "está hecho un desastre" | Recargo por estado |
| "huele a perro" | Ozonizado + Pelos de mascota |
| "se me volcó el café" | Remoción de manchas puntuales |
| "cuánto me sale" | Pedido de cotización |

## 13. Normalización y búsqueda tolerante

Los sinónimos de las tablas son datos, no la solución. La búsqueda necesita estas reglas.

**Normalización del texto** (misma función para el término indexado y para la consulta):

1. Minúsculas.
2. Quitar tildes y diéresis (`unaccent` en Postgres); `ñ → n`.
3. Quitar signos y colapsar espacios: *pre-lavado*, *pre lavado* y *prelavado* colapsan al mismo token.
4. Singularizar sufijos `s` / `es` para el matching, nunca para mostrar.

**Capas de coincidencia, en orden:**

1. Exacta sobre el término normalizado.
2. Exacta sobre la lista de sinónimos.
3. Prefijo (el dueño escribe mientras busca).
4. Similitud por trigramas (`pg_trgm`) para errores de tipeo: *detaling*, *ceramico*, *pulija*.

**Mapeo defecto → servicio.** La tabla de la sección 5 es una relación de muchos a muchos, no una
columna de texto. Buscar un defecto devuelve los servicios que lo resuelven, con el defecto como motivo
sugerido para el job.

**Errores de tipeo frecuentes a sembrar en el índice:** ceramico, detaling, detailin, polarisado,
pulicion, tapiceria, aspiradura, limpieza interio, cera carnauba, ppf film.

**Reglas de idioma.** Cada término entra al índice en español y en inglés con el mismo peso. El dueño
escribe en español, pero el rubro copia nombres en inglés de proveedores y de YouTube: *coating*, *clay*,
*wrap*, *detailing*.
