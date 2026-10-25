"""Corre los dos escenarios del motor y genera salida/resultados.json + salida/dashboard.html.

Uso:  .venv/bin/python correr.py [--presupuesto 200] [--replicas 40]
  A) "Ahora": estado en vivo (GBFS) y demanda de las próximas 4 h → órdenes de movimiento.
  B) "Plan nocturno": distribución óptima para el siguiente día hábil 06-12 h con la flota actual.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import motor as m

SALIDA = Path(__file__).parent / "salida"


def estado(f, x, t):
    """falta: le faltan bicis y eso cuesta viajes; sobra: puede ceder bicis sin costo relevante."""
    if x < t and f[x] - f[t] > 0.25:
        return "falta"
    if x > t + 1:
        return "sobra"
    return "ok"


def resumen_estaciones(est, curvas, objetivo, ideal, x0, x_plan, col_cap):
    filas = []
    for c in est.index:
        f = curvas.total(c)
        t = int(objetivo[c])
        filas.append(dict(
            codigo=c, nombre=est.at[c, "nombre"], lat=est.at[c, "lat"], lon=est.at[c, "lon"],
            cap=int(est.at[c, col_cap]), bicis=int(x0[c]), objetivo=t, ideal=ideal[c][0],
            despues=int(x_plan[c]), estado=estado(f, int(x0[c]), t),
            perdida_actual=round(float(f[x0[c]]), 2), perdida_despues=round(float(f[x_plan[c]]), 2),
            perdida_objetivo=round(float(f[t]), 2),
            retiros_perdidos=round(float(curvas.perd_ret[c][x0[c]]), 2),
            devol_rechazadas=round(float(curvas.perd_dev[c][x0[c]]), 2),
        ))
    return filas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presupuesto", type=int, default=200, help="bicis que la flota de camiones puede mover en el horizonte")
    ap.add_argument("--replicas", type=int, default=40)
    ap.add_argument("--viajes", default="viajes_2026-09.csv")
    args = ap.parse_args()

    est = m.cargar_estaciones()
    est = est[est.activa & (est.cap_util > 0)]
    viajes = m.cargar_viajes(args.viajes)
    ahora = datetime.now()
    salida = dict(generado=ahora.isoformat(timespec="minutes"), fuente_viajes=args.viajes,
                  estaciones=len(est), viajes_usados=len(viajes))

    # ---------------- Escenario A: ahora, próximas 4 horas
    habil_hoy = ahora.weekday() < 5
    horas_a = list(range(ahora.hour, ahora.hour + 4))
    lam, mu, dias = m.tasas(viajes, est.index, habil_hoy, horas_a)
    cur = m.curvas_estaciones(est, lam, mu, "cap_util")
    obj = m.objetivo_por_estacion(cur, est)  # óptimo sin límite de flota (referencia)
    x0 = est.bicis.astype(int)
    _, pasos_a = m.asignar_flota(cur, est, "cap_util")
    meta = m.distribucion_con_flota(pasos_a, est, int(x0.sum()))  # mejor reparto de las bicis que hay
    sens = []
    for b in [0, 50, 100, 200, 400, 800, 1600]:
        mov_b = m.plan_rebalanceo(cur, est, x0, meta, b, limite_s=20)
        xb = m.aplicar(x0, mov_b)
        sens.append(dict(presupuesto=b, movidas=int(mov_b.bicis.sum()) if len(mov_b) else 0,
                         perdida_esperada=round(float(sum(cur.total(c)[xb[c]] for c in est.index)), 1)))
    mov = m.plan_rebalanceo(cur, est, x0, meta, args.presupuesto)
    x_plan = m.aplicar(x0, mov)
    sim_sin = m.simular(est, "cap_util", x0, viajes, habil_hoy, m.bloques_horarios(horas_a), args.replicas)
    sim_con = m.simular(est, "cap_util", x_plan, viajes, habil_hoy, m.bloques_horarios(horas_a), args.replicas)
    mov = mov.assign(
        nombre_origen=mov.origen.map(est.nombre), nombre_destino=mov.destino.map(est.nombre),
        lat_o=mov.origen.map(est.lat), lon_o=mov.origen.map(est.lon),
        lat_d=mov.destino.map(est.lat), lon_d=mov.destino.map(est.lon),
        ahorro=[round(float(cur.total(d)[x0[d]] - cur.total(d)[x_plan[d]]), 2) for d in mov.destino],
    ).sort_values("ahorro", ascending=False)
    salida["ahora"] = dict(
        ventana=f"{horas_a[0]:02d}:00–{(horas_a[-1] + 1) % 24:02d}:00", tipo_dia="hábil" if habil_hoy else "fin de semana",
        dias_promediados=int(dias), presupuesto=args.presupuesto,
        demanda_esperada=round(float(lam.values.sum() + mu.values.sum()), 0),
        bicis_disponibles=int(x0.sum()),
        perdida_sin_plan=round(float(sum(cur.total(c)[x0[c]] for c in est.index)), 1),
        perdida_con_plan=round(float(sum(cur.total(c)[x_plan[c]] for c in est.index)), 1),
        perdida_meta=round(float(sum(cur.total(c)[meta[c]] for c in est.index)), 1),
        perdida_ideal=round(float(sum(cur.total(c)[obj[c][0]] for c in est.index)), 1),
        bicis_para_ideal=int(sum(obj[c][0] for c in est.index)),
        sensibilidad=sens, sim_sin=sim_sin, sim_con=sim_con,
        movimientos=mov.to_dict("records"),
        estaciones=resumen_estaciones(est, cur, meta, obj, x0, x_plan, "cap_util"),
    )

    # ---------------- Escenario B: plan nocturno para el siguiente día hábil 06–12 h
    horas_b = list(range(6, 12))
    lam_b, mu_b, dias_b = m.tasas(viajes, est.index, True, horas_b)
    cur_b = m.curvas_estaciones(est, lam_b, mu_b, "cap_util")
    obj_b = m.objetivo_por_estacion(cur_b, est)
    curva, pasos = m.asignar_flota(cur_b, est, "cap_util")
    # flota operativa = bicis disponibles ahora + viajes en curso (tasa de retiro actual × duración media)
    en_curso = int(round(mu.iloc[:, 0].sum() * viajes.dur_h.mean()))
    flota = int(x0.sum()) + en_curso
    x_opt = m.distribucion_con_flota(pasos, est, flota)
    capu = est.cap_util.astype(int)
    x_prop = np.minimum(np.round(capu * flota / capu.sum()).astype(int), capu)
    perd = lambda x: float(sum(cur_b.total(c)[x[c]] for c in est.index))
    sim_prop = m.simular(est, "cap_util", x_prop, viajes, True, m.bloques_horarios(horas_b), args.replicas)
    sim_opt = m.simular(est, "cap_util", x_opt, viajes, True, m.bloques_horarios(horas_b), args.replicas)
    paso = max(1, len(curva) // 200)
    salida["plan_nocturno"] = dict(
        ventana="06:00–12:00 día hábil", dias_promediados=int(dias_b), flota=flota, viajes_en_curso_estimados=en_curso,
        demanda_esperada=round(float(lam_b.values.sum() + mu_b.values.sum()), 0),
        perdida_proporcional=round(perd(x_prop), 1), perdida_optima=round(perd(x_opt), 1),
        flota_saturacion=len(pasos), perdida_saturacion=round(curva[-1][1], 1),
        curva_flota=[dict(flota=f, perdida=round(p, 1)) for f, p in curva[::paso]],
        sim_proporcional=sim_prop, sim_optima=sim_opt,
        estaciones=[dict(codigo=c, nombre=est.at[c, "nombre"], lat=est.at[c, "lat"], lon=est.at[c, "lon"],
                         cap=int(capu[c]), proporcional=int(x_prop[c]), optimo=int(x_opt[c]),
                         llenado_pct=round(100 * x_opt[c] / capu[c]),
                         retiros_6_12=round(float(mu_b.loc[c].sum()), 1), devol_6_12=round(float(lam_b.loc[c].sum()), 1))
                    for c in est.index],
    )

    SALIDA.mkdir(exist_ok=True)
    datos = json.dumps(salida, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    (SALIDA / "resultados.json").write_text(datos)
    plantilla = (Path(__file__).parent / "dashboard_plantilla.html").read_text()
    (SALIDA / "dashboard.html").write_text(plantilla.replace("/*__DATOS__*/null", datos))
    a, b = salida["ahora"], salida["plan_nocturno"]
    print(f"A {a['ventana']} ({a['tipo_dia']}): pérdida CTMC sin plan {a['perdida_sin_plan']} → con plan {a['perdida_con_plan']} "
          f"(ideal {a['perdida_ideal']}); sim sin {sim_sin} | con {sim_con}; movimientos {len(mov)} / {int(mov.bicis.sum())} bicis")
    print(f"B flota {flota}: proporcional {b['perdida_proporcional']} vs óptima {b['perdida_optima']}; saturación {len(pasos)} bicis; "
          f"sim prop {sim_prop} | opt {sim_opt}")


if __name__ == "__main__":
    main()
