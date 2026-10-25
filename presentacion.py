"""Presentación HTML (diapositivas) de cómo reparte el motor, desde salida/barrido.json.

Uso:  .venv/bin/python presentacion.py   →  salida/presentacion.html
Autocontenida: mapas dibujados en el propio HTML, sin teselas ni internet.
"""
import json
import re
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).parent
corto = lambda s: re.sub(r"^CE-[\d-]+\s*", "", s).strip()


def main():
    D = json.loads((RAIZ / "salida" / "barrido.json").read_text())
    E, T, B = D["estaciones"], D["totales"], D["por_bloque"]
    S = T["sim"]
    nb = len(D["bloques"])
    tau = np.array([int(b[6:8]) * 60 + int(b[9:11]) - int(b[:2]) * 60 - int(b[3:5]) for b in D["bloques"]]) / 60
    man, tar = slice(0, 7), slice(20, nb)  # 06:00–09:51 y 17:00–19:00
    neto = lambda e, s: float(((np.array(e["mu"]) - np.array(e["lam"])) * tau)[s].sum())
    PF = D["optimizacion"]["perdida_por_flota"]
    flota = D["flota"]
    for e in E:
        e["delta"] = e["curva"][e["x06_prop"]] - e["curva"][e["x06"]]

    salida = max((e for e in E if .25 * e["cap"] < e["x06"] < .75 * e["cap"]), key=lambda e: e["delta"])
    llegada = max((e for e in E if e["x06"] == 0), key=lambda e: e["delta"])
    media = min((e for e in E if .35 * e["cap"] < e["x06"] < .65 * e["cap"] and e["cap"] >= 25),
                key=lambda e: abs(e["x06"] - e["x06_prop"]) - e["curva"][0] / 100)
    ejemplo = lambda e, tipo: dict(nombre=corto(e["nombre"]), tipo=tipo, cap=e["cap"], parejo=e["x06_prop"], motor=e["x06"],
                                   curva=[round(v, 2) for v in e["curva"]])
    llen = [e for e in E if e["x06"] / e["cap"] >= .7]
    vac = [e for e in E if e["x06"] == 0]
    pico = sum(B["perdida_optima_06"][3:7]) + sum(B["perdida_optima_06"][20:])
    nm = [neto(e, man) for e in E]
    nt = [neto(e, tar) for e in E]
    i_bv = int(np.argmax(nm))

    datos = dict(
        dias=D["dias_habiles"], desde=D["primer_dia"], hasta=D["ultimo_dia"], feriados=D["feriados_excluidos"],
        viajes=D["viajes_en_ventana"], flota=flota, nsat=len(D["optimizacion"]["orden"]), paso=D["paso_min"],
        n_est=len(E), n_esc=len(D["escenarios"]),
        tot=dict(parejo=T["perdida_proporcional"], motor=T["perdida_optima_06"], continuo=T["perdida_dinamica_perfecta"],
                 sim_parejo=round(S["parejo"]["retiros_perdidos"] + S["parejo"]["devoluciones_rechazadas"]),
                 sim_motor=round(S["plan_mezcla"]["retiros_perdidos"] + S["plan_mezcla"]["devoluciones_rechazadas"]),
                 serv_parejo=round(S["parejo"]["viajes_servidos"]), serv_motor=round(S["plan_mezcla"]["viajes_servidos"]),
                 retiros=round(sum(B["retiros"])), devoluciones=round(sum(B["devoluciones"])),
                 pico_pct=round(100 * pico / T["perdida_optima_06"]), poisson_vs_mezcla=T["bicis_que_cambian"]),
        marg_hoy=round(PF[flota] - PF[flota + 1], 2),  # mismo redondeo que el PDF
        # estaciones: lat, lon, cap, bicis motor, bicis parejo, neto mañana, neto tarde
        est=[[round(e["lat"], 5), round(e["lon"], 5), e["cap"], e["x06"], e["x06_prop"], round(a, 1), round(b, 1)]
             for e, a, b in zip(E, nm, nt)],
        orden=D["optimizacion"]["orden"], pf=PF, nombres=[corto(e["nombre"]) for e in E],
        ejemplos=[ejemplo(salida, "De salida"), ejemplo(media, "Mixta"), ejemplo(llegada, "De llegada")],
        buenavista=dict(nombre=corto(E[i_bv]["nombre"]), man=round(nm[i_bv]), tar=round(nt[i_bv])),
        n_llenar=len(llen), bicis_llenar=sum(e["x06"] for e in llen), n_vacias=len(vac),
        top_llenar=[dict(n=corto(e["nombre"]), p=e["x06_prop"], m=e["x06"], c=e["cap"])
                    for e in sorted(E, key=lambda e: -(e["x06"] - e["x06_prop"]))[:6]],
        top_vaciar=[dict(n=corto(e["nombre"]), p=e["x06_prop"], m=e["x06"], c=e["cap"])
                    for e in sorted(E, key=lambda e: e["x06"] - e["x06_prop"])[:6]],
        bloques=[b[:5] for b in D["bloques"]],
        perd_parejo=B["perdida_proporcional"], perd_motor=B["perdida_optima_06"], perd_cont=B["perdida_dinamica"],
    )
    plantilla = (RAIZ / "presentacion_plantilla.html").read_text()
    destino = RAIZ / "salida" / "presentacion.html"
    destino.write_text(plantilla.replace("/*__DATOS__*/null", json.dumps(datos, ensure_ascii=False)))
    print(destino, destino.stat().st_size)


if __name__ == "__main__":
    main()
