"""Motor de rebalanceo Ecobici CDMX basado en cadenas de Markov de tiempo continuo.

Cada estación es un proceso de nacimiento y muerte sobre el número de bicis
x ∈ {0..C}: llegan bicis (devoluciones) a tasa λ(t) y salen (retiros) a tasa μ(t),
constantes por hora. Con la solución transitoria exacta (exponencial de matriz)
se calcula, para cada inventario inicial x, la demanda que se pierde en el
horizonte: retiros con la estación vacía y devoluciones con la estación llena
(función de insatisfacción de Raviv & Kolka, 2013). Encima de eso:

  1. objetivo por estación  = argmin de la demanda perdida esperada;
  2. tamaño de flota        = asignación marginal codiciosa (óptima por convexidad);
  3. plan de rebalanceo     = programa lineal que mueve bicis de donde sobran a
                              donde faltan, con presupuesto de bicis movidas y
                              costo por distancia;
  4. validación             = simulación de eventos discretos de la red completa
                              con viajes origen-destino reales (bootstrap).
"""
from __future__ import annotations

import heapq
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.vq import kmeans2
from scipy.linalg import expm
from scipy.optimize import Bounds, LinearConstraint, milp

DATOS = Path(__file__).parent / "data"
PESO_DEVOLUCION = 1.0  # una devolución rechazada pesa lo mismo que un retiro perdido


# ---------------------------------------------------------------- núcleo CTMC
def bloque_ctmc(cap: int, lam: float, mu: float, tau: float):
    """P = e^{Qτ} y las columnas "vacía" y "llena" de M = ∫_0^τ e^{Qs} ds, con tasas por hora.

    Truco de Van Loan con B = [e_0, e_C]: expm([[Q, B],[0, 0]]·τ) = [[P, M·B],[0, I]].
    Solo se necesitan esas dos columnas (tiempo esperado vacía y llena), así que la
    matriz aumentada es de n+2 y no de 2n. M[:, 0] = vacía, M[:, -1] = llena.
    """
    n = cap + 1
    Q = np.zeros((n, n))
    idx = np.arange(n - 1)
    Q[idx, idx + 1] = lam
    Q[idx + 1, idx] = mu
    Q[np.arange(n), np.arange(n)] = -Q.sum(axis=1)
    A = np.zeros((n + 2, n + 2))
    A[:n, :n] = Q
    A[0, n] = 1
    A[n - 1, n + 1] = 1
    E = expm(A * tau)
    return E[:n, :n], E[:n, n:]


def demanda_perdida(cap: int, lam, mu, tau=1.0):
    """Retiros y devoluciones perdidos esperados en el horizonte, por inventario inicial.

    lam, mu: tasas por hora, una entrada por bloque. tau: duración de cada bloque
    en horas (un número para todos o una lista). Devuelve dos vectores de largo
    cap+1 (índice = bicis al inicio).
    """
    n = cap + 1
    taus = np.broadcast_to(np.asarray(tau, dtype=float), (len(lam),))
    v_ret, v_dev = np.zeros(n), np.zeros(n)
    for l, m, t in zip(lam[::-1], mu[::-1], taus[::-1]):  # recursión hacia atrás
        P, M = bloque_ctmc(cap, l, m, t)
        v_ret = M[:, 0] * m + P @ v_ret
        v_dev = M[:, -1] * l + P @ v_dev
    return np.maximum(v_ret, 0), np.maximum(v_dev, 0)


def demanda_perdida_mezcla(cap: int, lam, mu, tau, perfiles, pesos):
    """Proceso de Poisson con tasa aleatoria por día (proceso de Cox discreto).

    Escenario q: las tasas del bloque k se multiplican por perfiles[q, k]; pesa pesos[q].
    La pérdida esperada es el promedio ponderado; sigue siendo convexa en x porque
    es una suma ponderada de funciones convexas.
    """
    lam, mu = np.asarray(lam, float), np.asarray(mu, float)
    ret, dev = np.zeros(cap + 1), np.zeros(cap + 1)
    for th, w in zip(perfiles, pesos):
        r, d = demanda_perdida(cap, lam * th, mu * th, tau)
        ret += w * r
        dev += w * d
    return ret, dev


def escenarios_dia(viajes: pd.DataFrame, bloques, n: int, semilla: int = 7):
    """Escenarios de día a partir de los días reales.

    factor[k, d] = retiros del sistema en el bloque k del día d / promedio del bloque k.
    Se agrupan los perfiles diarios (vector de 24 bloques) con k-means: cada grupo es
    un escenario con su perfil promedio y su peso (fracción de días). Captura nivel
    (temporada, lunes) y forma (p. ej. tardes con lluvia).
    """
    k = bloque_de(viajes.ini, bloques)
    v = viajes.assign(k=k, dia=viajes.ini.dt.normalize())
    tot = v[v.k >= 0].groupby(["k", "dia"]).size().unstack(fill_value=0)
    tot = tot.reindex(index=range(len(bloques)), fill_value=0)
    factor = tot.div(tot.mean(axis=1), axis=0)            # bloques × días, media 1 por bloque
    X = factor.T.values                                      # días × bloques
    centros, etiqueta = kmeans2(X, n, seed=semilla, minit="++")
    usados = np.unique(etiqueta)
    perfiles = np.array([X[etiqueta == q].mean(axis=0) for q in usados])
    pesos = np.array([(etiqueta == q).mean() for q in usados])
    grupo = pd.Series(np.searchsorted(usados, etiqueta), index=factor.columns)
    return perfiles, pesos, factor, grupo


# ---------------------------------------------------------------- datos
def _norm(codigo: str) -> str:
    return str(codigo).split("-")[0].lstrip("0") or "0"


def cargar_estaciones():
    info = json.loads((DATOS / "station_information.json").read_text())["data"]["stations"]
    stat = {s["station_id"]: s for s in json.loads((DATOS / "station_status.json").read_text())["data"]["stations"]}
    filas = []
    for s in info:
        e = stat.get(s["station_id"], {})
        activa = e.get("is_renting", 0) == 1 and e.get("is_returning", 0) == 1
        bicis = e.get("num_bikes_available", 0)
        filas.append(dict(
            codigo=_norm(s["short_name"]), nombre=s["name"], lat=s["lat"], lon=s["lon"],
            capacidad=s["capacity"], bicis=bicis, activa=activa,
            # muelles útiles ahora: los ocupados por bicis dañadas no sirven
            cap_util=bicis + e.get("num_docks_available", 0),
        ))
    return pd.DataFrame(filas).drop_duplicates("codigo").set_index("codigo")


def cargar_viajes(archivo: str, feriados=frozenset()):
    """Viajes de un archivo mensual. `habil` = lunes a viernes y no feriado."""
    df = pd.read_csv(DATOS / archivo, dtype=str)
    ini = pd.to_datetime(df.Fecha_Retiro + " " + df.Hora_Retiro, format="%d/%m/%Y %H:%M:%S", errors="coerce")
    fin = pd.to_datetime(df.Fecha_Arribo + " " + df.Hora_Arribo, format="%d/%m/%Y %H:%M:%S", errors="coerce")
    v = pd.DataFrame(dict(
        origen=df.Ciclo_Estacion_Retiro.map(_norm), destino=df.Ciclo_EstacionArribo.map(_norm),
        ini=ini, fin=fin,
    )).dropna()
    v["dur_h"] = (v.fin - v.ini).dt.total_seconds() / 3600
    # mes dominante del archivo y duraciones plausibles (1 min a 3 h)
    mes = v.ini.dt.to_period("M").mode()[0]
    v = v[(v.ini.dt.to_period("M") == mes) & v.dur_h.between(1 / 60, 3)]
    v["habil"] = (v.ini.dt.day_of_week < 5) & ~v.ini.dt.date.isin(feriados)
    return v


def bloques_dia(inicio_min: int, fin_min: int, paso_min: int):
    """Bloques [(inicio, duración)] en minutos desde medianoche; el último puede ser más corto."""
    return [(t, min(paso_min, fin_min - t)) for t in range(inicio_min, fin_min, paso_min)]


def bloque_de(ts: pd.Series, bloques):
    """Índice de bloque de cada instante (-1 si cae fuera de la ventana)."""
    minuto = ts.dt.hour * 60 + ts.dt.minute + ts.dt.second / 60
    ini = np.array([b[0] for b in bloques])
    k = np.searchsorted(ini, minuto.values, side="right") - 1
    fin = bloques[-1][0] + bloques[-1][1]
    return np.where((k >= 0) & (minuto.values < fin), k, -1)


def tasas_bloques(viajes: pd.DataFrame, codigos, bloques):
    """λ y μ por estación y bloque, en viajes/hora, promedio sobre los días de `viajes`.

    También devuelve los conteos diarios para medir dispersión (¿es Poisson?).
    """
    dias = viajes.ini.dt.normalize().nunique()
    horas = np.array([b[1] for b in bloques]) / 60
    kr, kd = bloque_de(viajes.ini, bloques), bloque_de(viajes.fin, bloques)
    v = viajes.assign(kr=kr, kd=kd, dia=viajes.ini.dt.normalize())
    cols = range(len(bloques))
    ret = v[v.kr >= 0].groupby(["origen", "kr"]).size().unstack(fill_value=0)
    dev = v[v.kd >= 0].groupby(["destino", "kd"]).size().unstack(fill_value=0)
    ret = ret.reindex(index=codigos, columns=cols, fill_value=0)
    dev = dev.reindex(index=codigos, columns=cols, fill_value=0)
    mu = ret / dias / horas
    lam = dev / dias / horas
    diario = v[v.kr >= 0].groupby(["origen", "kr", "dia"]).size()
    return lam, mu, dias, diario


def tasas(viajes: pd.DataFrame, codigos, habil: bool, horas):
    """λ (devoluciones) y μ (retiros) por estación y hora, promedio por día del tipo."""
    v = viajes[viajes.habil == habil]
    dias = v.ini.dt.normalize().nunique()
    mu = v.groupby([v.origen, v.ini.dt.hour]).size().unstack(fill_value=0)
    lam = v.groupby([v.destino, v.fin.dt.hour]).size().unstack(fill_value=0)
    horas = [h % 24 for h in horas]
    mu = mu.reindex(index=codigos, columns=range(24), fill_value=0)[horas] / dias
    lam = lam.reindex(index=codigos, columns=range(24), fill_value=0)[horas] / dias
    return lam, mu, dias


# ---------------------------------------------------------------- optimización
@dataclass
class Curvas:
    perd_ret: dict  # codigo -> vector por inventario inicial
    perd_dev: dict

    def total(self, c):
        return self.perd_ret[c] + PESO_DEVOLUCION * self.perd_dev[c]


def curvas_estaciones(est, lam, mu, col_cap):
    pr, pd_ = {}, {}
    for c in est.index:
        cap = int(est.at[c, col_cap])
        pr[c], pd_[c] = demanda_perdida(cap, lam.loc[c].values, mu.loc[c].values)
    return Curvas(pr, pd_)


def objetivo_por_estacion(curvas: Curvas, est):
    obj = {}
    for c in est.index:
        f = curvas.total(c)
        mejor = f.min()
        dentro = np.where(f <= mejor + 0.25)[0]  # banda: a ≤0.25 viajes del óptimo
        obj[c] = (int(np.argmin(f)), int(dentro.min()), int(dentro.max()))
    return obj


def asignar_flota(curvas: Curvas, est, col_cap):
    """Asignación marginal codiciosa: óptima porque cada curva es convexa.

    Devuelve la curva flota→demanda perdida y la asignación para cada tamaño.
    """
    x = {c: 0 for c in est.index}
    perdida = sum(curvas.total(c)[0] for c in est.index)
    heap = []
    for c in est.index:
        f = curvas.total(c)
        if len(f) > 1:
            heapq.heappush(heap, (f[1] - f[0], c))
    curva, pasos = [(0, perdida)], []
    while heap:
        delta, c = heapq.heappop(heap)
        if delta >= -1e-9:
            break  # agregar más bicis ya no reduce pérdidas en ninguna estación
        x[c] += 1
        perdida += delta
        curva.append((len(pasos) + 1, perdida))
        pasos.append(c)
        f = curvas.total(c)
        if x[c] + 1 < len(f):
            heapq.heappush(heap, (f[x[c] + 1] - f[x[c]], c))
    return curva, pasos


def distribucion_con_flota(pasos, est, flota):
    x = pd.Series(0, index=est.index)
    for c in pasos[:flota]:
        x[c] += 1
    return x


def haversine_km(lat1, lon1, lat2, lon2):
    r = np.radians
    a = np.sin(r(lat2 - lat1) / 2) ** 2 + np.cos(r(lat1)) * np.cos(r(lat2)) * np.sin(r(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def plan_rebalanceo(curvas: Curvas, est, x0, objetivo, presupuesto, costo_km=0.02, costo_parada=1.0,
                    lote_min=5, vecinos=12, radio_km=5.0, limite_s=60):
    """Programa entero mixto: qué bicis mover, de dónde a dónde, para minimizar pérdidas.

    Variables: y_ij bicis movidas (enteras, solo entre pares cercanos), a_jk k-ésima
    bici agregada a j, r_ik k-ésima bici quitada a i (por convexidad se llenan en
    orden, así que el costo es exacto), z_s visita a la estación s (binaria).
    Cada visita cuesta `costo_parada` viajes-equivalentes (tiempo de camión) y debe
    mover al menos `lote_min` bicis (o lo que falte/sobre si es menos).
    """
    obj_x = {c: int(objetivo[c]) for c in est.index}
    recept = [c for c in est.index if obj_x[c] > x0[c]]
    donant = [c for c in est.index if obj_x[c] < x0[c]]
    if not recept or not donant or presupuesto <= 0:
        return pd.DataFrame(columns=["origen", "destino", "bicis", "km"])
    dl = est.loc[donant]
    pares = []
    for j in recept:
        d = haversine_km(est.at[j, "lat"], est.at[j, "lon"], dl.lat.values, dl.lon.values)
        orden = np.argsort(d)[:vecinos]
        pares += [(donant[i], j, d[i]) for i in orden if d[i] <= radio_km]
    costo = [costo_km * d for (_, _, d) in pares]
    n_y = len(pares)
    var_a, var_r = [], []
    for j in recept:
        f = curvas.total(j)
        for k in range(x0[j], obj_x[j]):
            var_a.append(j); costo.append(f[k + 1] - f[k])        # negativo: ganancia
    for i in donant:
        f = curvas.total(i)
        for k in range(x0[i], obj_x[i], -1):
            var_r.append(i); costo.append(f[k - 1] - f[k])        # positivo o ~0: pérdida
    nodos = {c: t for t, c in enumerate(recept + donant)}
    n_z = len(nodos)
    costo += [costo_parada] * n_z
    n = len(costo)
    off_a, off_r, off_z = n_y, n_y + len(var_a), n_y + len(var_a) + len(var_r)
    filas, lo, hi = [], [], []
    flujo = np.zeros((n_z, n))  # Σ y que entra (receptor) o sale (donante) de cada nodo
    for t, (i, j, _) in enumerate(pares):
        flujo[nodos[j], t] = 1
        flujo[nodos[i], t] = 1
    # balance: Σ_k a_jk = Σ_i y_ij ;  Σ_k r_ik = Σ_j y_ij
    bal = -flujo.copy()
    for t, j in enumerate(var_a):
        bal[nodos[j], off_a + t] = 1
    for t, i in enumerate(var_r):
        bal[nodos[i], off_r + t] = 1
    filas.append(bal); lo += [0] * n_z; hi += [0] * n_z
    # visita: lote_s·z_s ≤ flujo_s ≤ max_s·z_s
    maximo = np.array([abs(obj_x[c] - x0[c]) for c in nodos])
    lote = np.minimum(lote_min, maximo)
    vis_hi = flujo.copy(); vis_hi[np.arange(n_z), off_z + np.arange(n_z)] = -maximo
    vis_lo = flujo.copy(); vis_lo[np.arange(n_z), off_z + np.arange(n_z)] = -lote
    filas += [vis_hi, vis_lo]; lo += [-np.inf] * n_z + [0] * n_z; hi += [0] * n_z + [np.inf] * n_z
    pres = np.zeros((1, n)); pres[0, :n_y] = 1
    filas.append(pres); lo.append(0); hi.append(presupuesto)
    integ = np.r_[np.ones(n_y), np.zeros(len(var_a) + len(var_r)), np.ones(n_z)]
    ub = np.r_[np.full(n_y, np.inf), np.ones(n - n_y)]
    res = milp(np.array(costo), constraints=LinearConstraint(np.vstack(filas), lo, hi), integrality=integ,
               bounds=Bounds(np.zeros(n), ub), options=dict(time_limit=limite_s, mip_rel_gap=0.01))
    if res.x is None:
        raise RuntimeError(f"MILP sin solución: {res.message}")
    y = np.round(res.x[:n_y]).astype(int)
    mov = pd.DataFrame([(i, j, int(b), round(d, 2)) for (i, j, d), b in zip(pares, y) if b > 0],
                       columns=["origen", "destino", "bicis", "km"])
    return mov


def aplicar(x0, mov):
    x = x0.copy()
    for r in mov.itertuples():
        x[r.origen] -= r.bicis
        x[r.destino] += r.bicis
    return x


# ---------------------------------------------------------------- simulación de red
def bloques_horarios(horas):
    return [((h % 24) * 60, 60) for h in horas]


def simular(est, col_cap, x_inicial, viajes, habil, bloques, replicas=40, semilla=7, perfiles=None):
    """Simulación de eventos discretos con viajes reales re-muestreados.

    Retiros: Poisson por estación y bloque (tasa observada). Destino y duración:
    se toman de un viaje real del mismo origen, tipo de día y bloque. Si el destino
    está lleno, la persona busca la estación más cercana con espacio (cuenta como
    devolución rechazada). Si el origen está vacío, el retiro se pierde.
    `bloques` = [(inicio_min, duración_min)] contiguos, sin cruzar medianoche.
    `perfiles` (opcional, días × bloques): en cada réplica se sortea un día real y
    sus multiplicadores escalan la demanda. Con la misma semilla, dos políticas ven
    los mismos días y los mismos retiros (números aleatorios comunes).
    """
    if perfiles is not None:
        perfiles = np.asarray(perfiles)
    rng = np.random.default_rng(semilla)
    cods = list(est.index)
    pos = {c: k for k, c in enumerate(cods)}
    cap = est[col_cap].values.astype(int)
    lat, lon = est.lat.values, est.lon.values
    v = viajes[viajes.habil == habil]
    dias = v.ini.dt.normalize().nunique()
    v = v.assign(k=bloque_de(v.ini, bloques))
    v = v[(v.k >= 0) & v.origen.isin(pos) & v.destino.isin(pos)]
    o = v.origen.map(pos)
    grupos = {key: (g.destino.map(pos).values, g.dur_h.values) for key, g in v.groupby([o, v.k])}
    conteo = v.groupby([o, v.k]).size() / dias  # retiros esperados por día en cada (estación, bloque)
    t_ini = np.array([(b[0] - bloques[0][0]) / 60 for b in bloques])
    dur_b = np.array([b[1] / 60 for b in bloques])
    horizonte = t_ini[-1] + dur_b[-1]
    vecinos = np.argsort(haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :]), axis=1)[:, 1:15]
    resultados = []
    for _ in range(replicas):
        x = np.array([x_inicial[c] for c in cods], dtype=int)
        theta = perfiles[rng.integers(len(perfiles))] if perfiles is not None else np.ones(len(bloques))
        eventos = []
        for (s, k), media in conteo.items():
            eventos += [(t_ini[k] + dur_b[k] * rng.random(), 0, s, k) for _ in range(rng.poisson(media * theta[k]))]
        heapq.heapify(eventos)
        perd_ret = perd_dev = servidos = 0
        while eventos:
            t, tipo, s, k = heapq.heappop(eventos)
            if tipo == 0:  # retiro
                if x[s] > 0:
                    x[s] -= 1; servidos += 1
                    dest, dur = grupos[(s, k)]
                    j = rng.integers(len(dest))
                    if t + dur[j] < horizonte:
                        heapq.heappush(eventos, (t + dur[j], 1, dest[j], k))
                else:
                    perd_ret += 1
            else:  # devolución
                if x[s] < cap[s]:
                    x[s] += 1
                else:
                    perd_dev += 1
                    alt = next((a for a in vecinos[s] if x[a] < cap[a]), None)
                    if alt is not None:
                        x[alt] += 1
        resultados.append((perd_ret, perd_dev, servidos))
    r = np.array(resultados)
    return dict(retiros_perdidos=r[:, 0].mean(), devoluciones_rechazadas=r[:, 1].mean(),
                viajes_servidos=r[:, 2].mean(), ic95_perdidos=1.96 * (r[:, 0] + r[:, 1]).std(ddof=1) / np.sqrt(len(r)),
                perdidos_por_replica=(r[:, 0] + r[:, 1]).tolist())
