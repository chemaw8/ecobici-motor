"""Barrido de día hábil 06:00–19:00 en bloques de 33 min con 3 meses de viajes.

Uso:  .venv/bin/python barrido.py [--flota 4759] [--paso 33] [--anticipacion 4] [--escenarios 6] [--replicas 30]

Modelo de demanda: Poisson con tasa aleatoria por día (mezcla / proceso de Cox).
Los 65 días hábiles reales se agrupan por su perfil de demanda en `escenarios`
grupos; cada estación usa la CTMC exacta en cada escenario y se promedia con su peso.
Se compara contra el modelo Poisson puro (tasa fija = promedio).

Qué calcula:
  1. λ y μ por estación y bloque; escenarios de día; dispersión observada vs predicha.
  2. Distribución óptima a las 06:00 (solo rebalanceo nocturno) con Poisson y con mezcla.
  3. Trayectoria esperada de cada estación durante el día bajo la mezcla.
  4. Meta por bloque (siguientes `anticipacion` bloques) y brecha meta − esperado.
  5. Simulación de red re-jugando días reales: parejo vs plan Poisson vs plan mezcla.
Salida: salida/barrido.json y salida/barrido.html
"""
import os

# Un hilo de álgebra lineal por proceso: con 8 procesos y BLAS multihilo la máquina se sobresatura.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import json  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from pathlib import Path

import holidays
import numpy as np
import pandas as pd

import motor as m

SALIDA = Path(__file__).parent / "salida"
ARCHIVOS = ["viajes_2026-07.csv", "viajes_2026-08.csv", "viajes_2026-09.csv"]


# ---------------------------------------------------------------- trabajo por estación (procesos)
def _bloques(cap, lam, mu, taus):
    return [m.bloque_ctmc(cap, lam[k], mu[k], taus[k]) for k in range(len(taus))]


def _costo(bl, lam, mu, k0, k1, n):
    """Pérdida esperada en bloques k0..k1-1 por inventario al inicio de k0."""
    v = np.zeros(n)
    for k in range(k1 - 1, k0 - 1, -1):
        P, M = bl[k]
        v = M[:, 0] * mu[k] + M[:, -1] * lam[k] + P @ v
    return v


def _trayectoria(bl, lam, mu, x0, n):
    nb = len(bl)
    E, pe, pf, perd = (np.zeros(nb) for _ in range(4))
    p = np.zeros(n); p[x0] = 1
    for k, (P, M) in enumerate(bl):
        E[k], pe[k], pf[k] = p @ np.arange(n), p[0], p[-1]
        perd[k] = p @ (M[:, 0] * mu[k] + M[:, -1] * lam[k])
        p = p @ P
    return E, pe, pf, perd


def pasada1(a):
    """Curvas de costo (Poisson y mezcla), costos por ventana (mezcla) y pérdida del reparto parejo."""
    cap, lam, mu, taus, perfiles, pesos, anticip, x_prop = a
    n, nb = cap + 1, len(taus)
    full_p = _costo(_bloques(cap, lam, mu, taus), lam, mu, 0, nb, n)
    full_m, vent, perd_prop = np.zeros(n), np.zeros((nb, n)), np.zeros(nb)
    for th, w in zip(perfiles, pesos):
        l, u = lam * th, mu * th
        bl = _bloques(cap, l, u, taus)
        full_m += w * _costo(bl, l, u, 0, nb, n)
        for k in range(nb):
            vent[k] += w * _costo(bl, l, u, k, min(nb, k + anticip), n)
        perd_prop += w * _trayectoria(bl, l, u, x_prop, n)[3]
    return full_p, full_m, vent, perd_prop


def pasada2(a):
    """Trayectoria bajo la mezcla para el plan mezcla y el plan Poisson; pérdida con rebalanceo continuo."""
    cap, lam, mu, taus, perfiles, pesos, x_m, x_p, meta = a
    n, nb = cap + 1, len(taus)
    E, pe, pf, perd, perd_pp, din = (np.zeros(nb) for _ in range(6))
    for th, w in zip(perfiles, pesos):
        l, u = lam * th, mu * th
        bl = _bloques(cap, l, u, taus)
        e_, pe_, pf_, perd_ = _trayectoria(bl, l, u, x_m, n)
        E += w * e_; pe += w * pe_; pf += w * pf_; perd += w * perd_
        perd_pp += w * _trayectoria(bl, l, u, x_p, n)[3]
        din += w * np.array([bl[k][1][meta[k], 0] * u[k] + bl[k][1][meta[k], -1] * l[k] for k in range(nb)])
    perd_p_propio = _trayectoria(_bloques(cap, lam, mu, taus), lam, mu, x_p, n)[3]
    return E, pe, pf, perd, perd_pp, din, perd_p_propio


def asignar(curvas_tot: dict, flota: int):
    """Mejor reparto de `flota` bicis dadas curvas convexas de pérdida por estación."""
    cur = m.Curvas(dict(curvas_tot), {c: np.zeros_like(f) for c, f in curvas_tot.items()})
    idx = pd.DataFrame(index=pd.Index(list(curvas_tot)))
    curva, pasos = m.asignar_flota(cur, idx, None)
    return m.distribucion_con_flota(pasos, idx, flota), curva, pasos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flota", type=int, default=4759, help="bicis operativas en estaciones a las 06:00")
    ap.add_argument("--paso", type=int, default=33, help="minutos por bloque")
    ap.add_argument("--anticipacion", type=int, default=4, help="bloques que mira hacia adelante la meta dinámica")
    ap.add_argument("--escenarios", type=int, default=6, help="grupos de días para la mezcla")
    ap.add_argument("--replicas", type=int, default=30)
    args = ap.parse_args()

    # ---------------- datos
    viajes = pd.concat([m.cargar_viajes(a) for a in ARCHIVOS], ignore_index=True)
    fechas = sorted(set(viajes.ini.dt.date))
    feriados = {d for d in fechas if d in holidays.Mexico(years={d.year for d in fechas})}
    viajes["habil"] = (viajes.ini.dt.day_of_week < 5) & ~viajes.ini.dt.date.isin(feriados)
    habiles = viajes[viajes.habil]
    feriados_habiles = sorted(d for d in feriados if d.weekday() < 5)

    est = m.cargar_estaciones()
    est = est[est.activa & (est.cap_util > 0)]
    cods = list(est.index)
    cap = est.cap_util.astype(int)
    bloques = m.bloques_dia(6 * 60, 19 * 60, args.paso)
    nb = len(bloques)
    taus = np.array([b[1] for b in bloques]) / 60
    etiquetas = [f"{b[0] // 60:02d}:{b[0] % 60:02d}–{(b[0] + b[1]) // 60:02d}:{(b[0] + b[1]) % 60:02d}" for b in bloques]
    lam, mu, dias, diario = m.tasas_bloques(habiles, cods, bloques)

    # ---------------- escenarios de día
    perfiles, pesos, factor, grupo = m.escenarios_dia(habiles, bloques, args.escenarios)
    fechas_g = pd.to_datetime(grupo.index)
    manana, tarde = slice(0, 8), slice(20, nb)  # 06:00–10:24 y 17:00–19:00
    escenarios = []
    for q in np.argsort(-perfiles.mean(axis=1)):
        sel = (grupo == q).values
        escenarios.append(dict(
            peso=round(float(pesos[q]), 3), dias=int(sel.sum()), factor=round(float(perfiles[q].mean()), 2),
            manana=round(float(perfiles[q][manana].mean()), 2), tarde=round(float(perfiles[q][tarde].mean()), 2),
            julio=int((fechas_g[sel].month == 7).sum()), agosto=int((fechas_g[sel].month == 8).sum()),
            septiembre=int((fechas_g[sel].month == 9).sum()), lunes=int((fechas_g[sel].day_of_week == 0).sum()),
            ejemplos=[str(d.date()) for d in fechas_g[sel][:3]]))

    # ---------------- dispersión observada vs predicha (retiros diarios por estación y bloque)
    suma = diario.groupby(level=[0, 1]).sum()
    suma2 = (diario ** 2).groupby(level=[0, 1]).sum()
    media = suma / dias
    celdas = media[media >= 1].index
    obs = ((suma2 / dias - media ** 2)[celdas] * dias / (dias - 1)) / media[celdas]
    k_celda = celdas.get_level_values(1)
    var_dias = factor.var(axis=1, ddof=1).values             # varianza del factor real por bloque
    var_esc = (pesos[:, None] * (perfiles - (pesos @ perfiles)) ** 2).sum(axis=0)
    pred_dias = 1 + media[celdas].values * var_dias[k_celda]
    pred_esc = 1 + media[celdas].values * var_esc[k_celda]
    dispersion = dict(
        celdas=int(len(obs)), observada=round(float(obs.median()), 2), obs_mayor_1_5=round(100 * float((obs > 1.5).mean()), 1),
        poisson=1.0, mezcla_dias_reales=round(float(np.median(pred_dias)), 2),
        mezcla_escenarios=round(float(np.median(pred_esc)), 2),
        mezcla_mayor_1_5=round(100 * float((pred_esc > 1.5).mean()), 1))

    # ---------------- pasada 1: curvas
    x_prop = pd.Series(np.minimum(np.round(cap * args.flota / cap.sum()).astype(int), cap), index=cods)
    filas = [(int(cap[c]), lam.loc[c].values, mu.loc[c].values, taus, perfiles, pesos, args.anticipacion, int(x_prop[c]))
             for c in cods]
    with ProcessPoolExecutor() as ex:
        r1 = list(ex.map(pasada1, filas, chunksize=8))
    full_p = {c: r[0] for c, r in zip(cods, r1)}
    full_m = {c: r[1] for c, r in zip(cods, r1)}
    perd_prop = np.array([r[3] for r in r1])
    x_p, _, _ = asignar(full_p, args.flota)
    x_m, curva, pasos = asignar(full_m, args.flota)
    metas = np.zeros((len(cods), nb), dtype=int)
    for k in range(nb):
        metas[:, k] = asignar({c: r[2][k] for c, r in zip(cods, r1)}, args.flota)[0].values

    # ---------------- pasada 2: trayectorias
    filas = [(int(cap[c]), lam.loc[c].values, mu.loc[c].values, taus, perfiles, pesos, int(x_m[c]), int(x_p[c]), metas[i])
             for i, c in enumerate(cods)]
    with ProcessPoolExecutor() as ex:
        r2 = list(ex.map(pasada2, filas, chunksize=8))
    E_o, pe_o, pf_o, perd_o, perd_pp, perd_din, perd_p_propio = (np.array([r[j] for r in r2]) for j in range(7))
    brecha = metas - E_o

    # ---------------- simulación re-jugando días reales (mismos días y retiros para las 3 políticas)
    dias_reales = factor.T.values
    sims = {nombre: m.simular(est, "cap_util", x, viajes, True, bloques, args.replicas, semilla=11, perfiles=dias_reales)
            for nombre, x in [("parejo", x_prop), ("plan_poisson", x_p), ("plan_mezcla", x_m)]}
    dif = np.array(sims["plan_poisson"]["perdidos_por_replica"]) - np.array(sims["plan_mezcla"]["perdidos_por_replica"])
    pareada = dict(media=round(float(dif.mean()), 1), ic95=round(float(1.96 * dif.std(ddof=1) / np.sqrt(len(dif))), 1),
                   replicas_a_favor=int((dif > 0).sum()), replicas=len(dif))
    cambio = pd.Series(x_m.values - x_p.values, index=cods)

    r = lambda a, d=2: np.round(a, d).tolist()
    salida = dict(
        modelo="mezcla", archivos=ARCHIVOS, dias_habiles=int(dias), feriados_excluidos=[str(d) for d in feriados_habiles],
        primer_dia=str(min(habiles.ini.dt.date)), ultimo_dia=str(max(habiles.ini.dt.date)),
        viajes_habiles=int(len(habiles)), viajes_en_ventana=int((m.bloque_de(habiles.ini, bloques) >= 0).sum()),
        paso_min=args.paso, anticipacion=args.anticipacion, flota=args.flota, bloques=etiquetas,
        escenarios=escenarios, dispersion=dispersion,
        totales=dict(
            perdida_proporcional=round(float(perd_prop.sum()), 0),
            perdida_plan_poisson=round(float(perd_pp.sum()), 0),
            perdida_optima_06=round(float(perd_o.sum()), 0),
            perdida_plan_poisson_segun_poisson=round(float(perd_p_propio.sum()), 0),
            perdida_dinamica_perfecta=round(float(perd_din.sum()), 0),
            flota_saturacion=len(pasos), perdida_saturacion=round(curva[-1][1], 0),
            bicis_que_cambian=int(cambio.abs().sum() // 2), estaciones_que_cambian=int((cambio != 0).sum()),
            sim=sims, sim_pareada_poisson_menos_mezcla=pareada,
            demanda=round(float(((lam + mu) * taus).values.sum()), 0)),
        por_bloque=dict(
            perdida_proporcional=r(perd_prop.sum(axis=0), 1), perdida_plan_poisson=r(perd_pp.sum(axis=0), 1),
            perdida_optima_06=r(perd_o.sum(axis=0), 1), perdida_dinamica=r(perd_din.sum(axis=0), 1),
            bicis_a_mover=r(np.clip(brecha, 0, None).sum(axis=0), 0),
            vacias=r((pe_o > 0.5).sum(axis=0), 0), llenas=r((pf_o > 0.5).sum(axis=0), 0),
            retiros=r((mu * taus).sum().values, 0), devoluciones=r((lam * taus).sum().values, 0)),
        curva_flota=[dict(flota=f, perdida=round(p, 1)) for f, p in curva[:: max(1, len(curva) // 200)]],
        # para ver la optimización en el HTML: orden en que el algoritmo asigna cada bici y pérdida total con N bicis
        optimizacion=dict(orden=[cods.index(c) for c in pasos], perdida_por_flota=[round(p, 3) for _, p in curva]),
        estaciones=[dict(
            codigo=c, nombre=est.at[c, "nombre"], lat=est.at[c, "lat"], lon=est.at[c, "lon"], cap=int(cap[c]),
            x06=int(x_m[c]), x06_poisson=int(x_p[c]), x06_prop=int(x_prop[c]),
            mu=r(mu.loc[c].values, 1), lam=r(lam.loc[c].values, 1),
            E=r(E_o[i], 1), pe=r(pe_o[i]), pf=r(pf_o[i]), meta=metas[i].tolist(), perd=r(perd_o[i]),
            curva=r(full_m[c]),  # viajes perdidos en el día según con cuántas bicis amanece (índice = bicis)
        ) for i, c in enumerate(cods)],
    )
    SALIDA.mkdir(exist_ok=True)
    datos = json.dumps(salida, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    (SALIDA / "barrido.json").write_text(datos)
    plantilla = (Path(__file__).parent / "barrido_plantilla.html").read_text()
    (SALIDA / "barrido.html").write_text(plantilla.replace("/*__DATOS__*/null", datos))

    t = salida["totales"]
    print(f"días hábiles {dias}, feriados excluidos {feriados_habiles}, viajes en ventana {salida['viajes_en_ventana']:,}")
    for e in escenarios:
        print("  escenario", e)
    print("dispersión", dispersion)
    print(f"pérdida esperada (bajo mezcla): parejo {t['perdida_proporcional']} | plan Poisson {t['perdida_plan_poisson']} "
          f"| plan mezcla {t['perdida_optima_06']} | continuo {t['perdida_dinamica_perfecta']}; "
          f"plan Poisson según Poisson {t['perdida_plan_poisson_segun_poisson']}")
    print(f"planes difieren en {t['bicis_que_cambian']} bicis / {t['estaciones_que_cambian']} estaciones; saturación {t['flota_saturacion']}")
    for k_, s in sims.items():
        print(f"  sim {k_}: perdidos {s['retiros_perdidos'] + s['devoluciones_rechazadas']:.0f} ±{s['ic95_perdidos']:.0f}, servidos {s['viajes_servidos']:.0f}")
    print("  sim pareada (Poisson − mezcla):", pareada)


if __name__ == "__main__":
    main()
