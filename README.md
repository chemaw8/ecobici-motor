# Motor de reparto Ecobici CDMX

Prototipo de referencia hecho en el **Chilangotón 2026** (2026-10-25) para el reto de
logística de Ecobici. Responde una pregunta: **¿con cuántas bicis debe amanecer cada
estación para perder la menor cantidad de viajes?** Usa cadenas de Markov de tiempo
continuo con datos públicos de Ecobici (julio a septiembre de 2026, 2.8 millones de viajes).

## Qué ver primero

| Archivo | Qué es |
|---|---|
| [`salida/presentacion.html`](salida/presentacion.html) | **Presentación de 11 láminas** (← → para avanzar, F pantalla completa). Funciona sin internet. Versión en línea: https://chemaw8.github.io/ecobici-motor/ |
| [`salida/reparto_ecobici.pdf`](salida/reparto_ecobici.pdf) | Reporte del reparto: resumen, mapas, método y anexo con las 672 estaciones |
| [`salida/barrido.html`](salida/barrido.html) | Tablero interactivo: cómo optimiza (bici por bici) y recorrido del día en bloques de 33 min |
| `motor.py` | Núcleo: CTMC por estación, asignación óptima, plan de camiones y simulación de red |

**Resultado principal (estimado por el modelo):** con la misma flota (4,759 bicis, supuesto),
acomodarlas con el motor en vez de repartirlas parejo baja los viajes perdidos de un día
hábil (06:00–19:00) de **7,746 a 6,169 (−20%)**. Una simulación que re-juega 65 días reales
confirma la mejora (10,048 → 8,252). Los viajes perdidos no se registran en los datos: son
estimaciones; ver la sección de límites.

## Cómo correrlo

Requiere Python 3.12+ y ~300 MB de disco para los datos.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # o: uv venv && uv pip install -r requirements.txt
bash descargar_datos.sh                     # viajes jul–sep 2026 y estado GBFS (fuentes oficiales)
.venv/bin/python -m pytest -q tests         # núcleo CTMC contra soluciones analíticas
.venv/bin/python barrido.py                 # modelo del día hábil, ~90 s → salida/barrido.{json,html}
.venv/bin/python reporte_pdf.py             # → salida/reparto_ecobici.pdf
.venv/bin/python presentacion.py            # → salida/presentacion.html
.venv/bin/python correr.py                  # versión inicial: "qué mover ahora" con el estado en vivo
```

Nota: `descargar_datos.sh` baja el estado **actual** de las estaciones; los resultados
cambiarán un poco respecto a los publicados aquí (estado del 2026-10-25).

**Fuentes de datos:** viajes de [Ecobici datos abiertos](https://ecobici.cdmx.gob.mx/datos-abiertos/)
y feed GBFS público (`gbfs.mex.lyftbikes.com`). Los datos no se incluyen en el repo; consulta
los términos de uso en el sitio de Ecobici.

---

# Bitácora técnica

## Cómo funciona el motor (4 capas)

| Capa | Qué hace | Método |
|---|---|---|
| 1. Demanda | Tasa de retiros μ y devoluciones λ por estación y hora | Viajes reales del mes / días del tipo (hábil o fin de semana) |
| 2. Estación | Viajes perdidos esperados para cada inventario inicial x | CTMC de nacimiento y muerte en {0..C}, solución transitoria exacta con `expm` del bloque [[Q, I],[0, 0]] (Raviv & Kolka 2013) |
| 3. Sistema | Mejor reparto de N bicis y curva "flota vs viajes perdidos" | Asignación marginal codiciosa (óptima porque cada curva es convexa) |
| 4. Operación | Órdenes de camión: de qué estación a cuál, cuántas bicis | Programa entero mixto (HiGHS): ganancia CTMC − costo por km − costo por parada, lote mínimo 5, presupuesto de bicis movidas |
| Validación | Comprobar que el plan funciona en la red, no solo estación por estación | Simulación de eventos discretos con viajes origen-destino reales re-muestreados |

## Resultados de la corrida del 2026-10-25 (estado en vivo a las 13:34)

- **Ahora (domingo 13–17 h, 3,960 bicis disponibles):** sin hacer nada se pierden ~1,346 viajes
  (CTMC). Si se mueven 200 bicis en 45 órdenes (0.9 km en promedio), la pérdida baja a ~1,124. La
  simulación de red da una mejora parecida: 286 viajes (IC95 aprox. ±26).
- **Plan nocturno (día hábil 06–12 h, flota ≈4,759):** con la misma flota, repartirla con el motor
  en vez de "todas al mismo % de llenado" baja los viajes perdidos de 4,518 a 2,816 (−38%). En la
  simulación, los viajes servidos pasan de ~14,000 a ~15,900.
- **Tamaño de flota:** por encima de ~9,500 bicis en estaciones a las 06:00 ya no se reduce la
  pérdida en la mañana; esa cifra es el techo y no una recomendación.

## Barrido de día hábil (`barrido.py` → `salida/barrido.html`)

```bash
curl -L -o data/viajes_2026-07.csv https://ecobici.cdmx.gob.mx/wp-content/uploads/2026/08/public_data_web_2026-07.csv
curl -L -o data/viajes_2026-08.csv https://ecobici.cdmx.gob.mx/wp-content/uploads/2026/09/public_data_web_2026-08_2.csv
uv pip install --python .venv/bin/python holidays
.venv/bin/python barrido.py --flota 4759 --paso 33 --anticipacion 4 --replicas 20   # ~70 s
```

- Datos: julio a septiembre de 2026, de lunes a viernes, sin feriados oficiales (librería
  `holidays`, México; en el periodo solo cae el 2026-09-16). Son 65 días y 2.84 millones de viajes
  entre 06:00 y 19:00.
- 24 bloques de 33 minutos (06:00–18:39) más un bloque final de 21 minutos (18:39–19:00).
- Por bloque: tasas λ y μ; trayectoria esperada de cada estación si solo se rebalancea de noche
  (propagación hacia adelante de la CTMC); meta con la flota para los siguientes 4 bloques; y la
  brecha entre ambas, que indica qué mover.
- Resultados de la corrida del 2026-10-25, viajes perdidos estimados entre 06:00 y 19:00:
  - repartir la flota parejo a las 06:00: 7,719;
  - repartirla de forma óptima a las 06:00: 6,135 (simulación de red: 10,185 → 8,235);
  - rebalanceo continuo perfecto, una cota ideal que exigiría mover cientos o miles de bicis por
    bloque: 299.
- Dispersión: la varianza/media de los retiros diarios tiene mediana 1.2 y es mayor de 1.5 en
  23% de las celdas. La demanda varía más entre días de lo que supone Poisson.
- **2026-10-25, modelo de mezcla (opción A).** `barrido.py` ahora modela la demanda como Poisson
  con tasa aleatoria por día. Los 65 días se agrupan con k-means en 6 escenarios: septiembre alto,
  normal con tarde alta, tarde baja, lunes, julio/agosto bajo con tarde muy baja, y un día atípico
  (2026-09-30). En cada escenario la CTMC se resuelve exacta y el resultado es el promedio ponderado.
  - Resultado: el plan nocturno con mezcla difiere del de Poisson en 4 bicis. Pérdida esperada:
    6,169 en ambos. En la simulación con días reales la diferencia pareada es −59 ± 690, no
    significativa.
  - Por qué: el factor común del día explica poco de la variabilidad por estación. La varianza/media
    predicha por la mezcla es 1.01–1.03 contra 1.20 observada. Los viajes en grupo (misma estación,
    ≤60 s, mismo destino) son el 4.4% de los retiros y explican ~1.05. El resto parece local:
    eventos, fallas de estación y la propia censura.
  - Rendimiento: la exponencial solo calcula las columnas vacía/llena de M (matriz aumentada n+2).
    Corrida completa: ~83 s con 8 procesos y un hilo BLAS por proceso.
- **2026-10-25: vista "Cómo se optimiza" en `barrido.html`.** Tiene tres partes:
  1. la curva de cada estación (viajes perdidos según bicis al amanecer), con el reparto parejo,
     el del motor y el mínimo;
  2. un deslizador que reparte las bicis una por una, igual que el algoritmo, con el valor marginal
     de cada bici;
  3. la tabla de dónde cambia el plan respecto a repartir parejo.

  El JSON incluye `optimizacion.orden` (orden de asignación), `optimizacion.perdida_por_flota` y
  `estaciones[].curva`.
- **2026-10-25: PDF del reparto** (`reporte_pdf.py` → `salida/reparto_ecobici.pdf`, 20 páginas). Se genera desde `salida/barrido.json`; los mapas se dibujan con las coordenadas de las estaciones, sin mapas base de terceros. Contiene: resumen, mapas del reparto y del cambio contra repartir parejo, cómo decide el motor, el día por bloques, límites y un anexo con las 672 estaciones.
- **2026-10-25: presentación** (`presentacion.py` → `salida/presentacion.html`, 11 láminas). Se navega con ← → y se pone en pantalla completa con F. Es autocontenida: los mapas se dibujan en canvas, sin teselas ni internet. Las cifras salen del mismo `barrido.json` que el PDF.
- Límite adicional: la CTMC por estación no conserva bicis. Las devoluciones de los retiros que
  se perdieron igual "llegan", así que a mediodía la suma esperada es ~5,360 contra 4,759 reales
  (+12%). Corregirlo requiere ajustar λ por la tasa de servicio de los orígenes (punto fijo).

## Supuestos y límites (decirlos en el pitch)

1. **Demanda censurada:** si una estación está vacía, nadie registra el retiro que no pudo hacer.
   Por eso las tasas subestiman la demanda en estaciones que se vacían seguido y las pérdidas son
   un piso. Corrección posible: estimar solo con periodos en que la estación tenía bicis (requiere
   historial GBFS).
2. **Estaciones independientes en la CTMC:** las devoluciones se tratan como Poisson externas. La
   simulación de red sí acopla origen y destino; compara **diferencias** y no niveles. En el
   escenario "ahora" la simulación tiene más pérdidas porque no incluye los viajes en curso a las 13:00.
3. **Movimiento instantáneo:** el plan supone que las bicis ya están movidas al inicio de la
   ventana. Falta modelar la ruta y los tiempos de los camiones (VRP, "cluster-first route-second"
   de Schuijbroek et al. 2017).
4. **Parámetros de operación inventados:** presupuesto de 200 bicis, lote mínimo de 5, costo por
   parada de 1 viaje y por km de 0.02. Hay que calibrarlos con el operador.
5. La flota operativa se estimó como bicis disponibles más viajes en curso. Las ~930 bicis dañadas
   en estaciones ocupan muelles y se descuentan de la capacidad útil.
