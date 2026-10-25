"""PDF del reparto de bicis a partir de salida/barrido.json.

Uso:  .venv/bin/python reporte_pdf.py   →  salida/reparto_ecobici.pdf
Los mapas se dibujan con las coordenadas de las estaciones, sin mapas base de terceros.
"""
import io
import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (Image as RLImage, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)

RAIZ = Path(__file__).parent
FUENTES = Path("/usr/share/fonts/TTF")
pdfmetrics.registerFont(TTFont("DV", FUENTES / "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", FUENTES / "DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DVC", FUENTES / "DejaVuSansCondensed.ttf"))
pdfmetrics.registerFont(TTFont("DVCB", FUENTES / "DejaVuSansCondensed-Bold.ttf"))
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False})

TINTA, GRIS, ACENTO = "#14213d", "#6b7280", "#f77f00"
ROJO, NARANJA, AZUL, VERDE, GRISC = "#d62828", "#f4a261", "#1d4ed8", "#2a9d8f", "#bdbdbd"

fmt = lambda n: f"{n:,.0f}"
nombre_corto = lambda s: re.sub(r"^CE-[\d-]+\s*", "", s).strip()
clave = lambda s: (re.match(r"^CE-([\d-]+)", s) or [None, ""])[1]


# ---------------------------------------------------------------- mapa (sin teselas: puntos + zonas de referencia)
ZONAS = [("BUENAVISTA", 19.4492, -99.1525), ("REFORMA", 19.4258, -99.1702), ("POLANCO", 19.4345, -99.1965),
         ("CENTRO", 19.4330, -99.1345), ("ROMA · CONDESA", 19.4150, -99.1665), ("NARVARTE", 19.3950, -99.1480),
         ("DEL VALLE", 19.3800, -99.1720), ("COYOACÁN", 19.3470, -99.1625)]


def figura_mapa(E, color, tam, titulo, leyenda):
    lat = np.array([e["lat"] for e in E]); lon = np.array([e["lon"] for e in E])
    kx = np.cos(np.radians(lat.mean()))
    x, y = lon * kx, lat
    fig, ax = plt.subplots(figsize=(7.2, 8.4))
    ax.set_facecolor("#F3F5F4")
    orden = np.argsort(-np.asarray(tam))
    ax.scatter(x[orden], y[orden], s=np.asarray(tam)[orden], c=np.asarray(color, dtype=object)[orden],
               alpha=.8, edgecolors="white", linewidths=.4)
    for nombre, la, lo in ZONAS:
        ax.text(lo * kx, la, nombre, ha="center", va="center", fontsize=8, fontweight="bold", color=TINTA, alpha=.7,
                path_effects=[__import__("matplotlib.patheffects", fromlist=["x"]).withStroke(linewidth=3, foreground="white")])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(titulo, loc="left", fontsize=11, color=TINTA, fontweight="bold")
    for etiqueta, c, s in leyenda:
        ax.scatter([], [], s=s, c=c, label=etiqueta, edgecolors="white")
    ax.legend(loc="lower left", frameon=True, fontsize=8, framealpha=.9)
    ax.text(.99, .005, "Cada punto es una estación Ecobici (posición del feed público GBFS)", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=6, color=GRIS)
    return fig


def a_imagen(fig, ancho_cm):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    w, h = Image.open(buf).size
    buf.seek(0)
    return RLImage(buf, width=ancho_cm * cm, height=ancho_cm * cm * h / w)


# ---------------------------------------------------------------- documento
def main():
    D = json.loads((RAIZ / "salida" / "barrido.json").read_text())
    E, T, B = D["estaciones"], D["totales"], D["por_bloque"]
    PF = D["optimizacion"]["perdida_por_flota"]
    flota, nsat = D["flota"], len(D["optimizacion"]["orden"])
    S = T["sim"]
    perd_sim = lambda s: s["retiros_perdidos"] + s["devoluciones_rechazadas"]
    for e in E:
        e["delta"] = e["curva"][e["x06_prop"]] - e["curva"][e["x06"]]
        e["llen"] = e["x06"] / e["cap"]

    # ---------- estilos
    st = lambda nombre, **kw: ParagraphStyle(nombre, fontName=kw.pop("f", "DV"), fontSize=kw.pop("s", 9.5),
                                             leading=kw.pop("l", 13), textColor=kw.pop("c", colors.HexColor(TINTA)), **kw)
    H1, H2 = st("h1", f="DVB", s=19, l=23, spaceAfter=4), st("h2", f="DVB", s=13, l=17, spaceBefore=10, spaceAfter=5)
    P, NOTA = st("p", spaceAfter=5, alignment=TA_LEFT), st("nota", s=8, l=10.5, c=colors.HexColor(GRIS), spaceAfter=4)
    SUB = st("sub", s=10.5, l=14, c=colors.HexColor(GRIS), spaceAfter=10)
    CELDA, CELDAB = st("c", f="DVC", s=7.6, l=9.2), st("cb", f="DVCB", s=7.6, l=9.2, c=colors.white)

    def tabla(filas, anchos, alinear_der=(), cabecera=True, zebra=True, tam=7.6):
        cuerpo = [[Paragraph(str(v), CELDAB if (cabecera and i == 0) else CELDA) for v in fila] for i, fila in enumerate(filas)]
        t = Table(cuerpo, colWidths=[a * cm for a in anchos], repeatRows=1 if cabecera else 0)
        estilo = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.2),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2), ("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#e5e7eb"))]
        if cabecera:
            estilo.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(TINTA)))
        if zebra:
            estilo += [("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f6f7f9")) for i in range(2, len(filas), 2)]
        for c in alinear_der:
            estilo.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
        t.setStyle(TableStyle(estilo))
        for i, fila in enumerate(cuerpo):
            for c in alinear_der:
                fila[c].style = ParagraphStyle("d", parent=fila[c].style, alignment=2)
        return t

    def kpis(items):
        celdas = [[Paragraph(f'<font name="DVB" size="15">{v}</font><br/><font size="7.5" color="{GRIS}">{t}</font>', st("k", l=17))
                   for v, t in items]]
        t = Table(celdas, colWidths=[(18.4 / len(items)) * cm] * len(items))
        t.setStyle(TableStyle([("BOX", (i, 0), (i, 0), .5, colors.HexColor("#e5e7eb")) for i in range(len(items))] +
                              [("LINEBEFORE", (0, 0), (0, 0), 3, colors.HexColor(ACENTO)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        return t

    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont("DV", 7); canvas.setFillColor(colors.HexColor(GRIS))
        canvas.drawString(1.6 * cm, 1.0 * cm, "Reparto de bicis Ecobici · prototipo de mentoría Chilangotón 2026 · cifras estimadas por modelo")
        canvas.drawRightString(letter[0] - 1.6 * cm, 1.0 * cm, f"página {doc.page}")
        canvas.restoreState()

    # ---------- figuras
    cat = lambda e: ROJO if e["llen"] >= .7 else NARANJA if e["llen"] >= .3 else AZUL if e["x06"] > 0 else GRISC
    fig_rep = figura_mapa(E, [cat(e) for e in E], [6 + e["x06"] * 2.2 for e in E],
                          f"Cómo amanecer: {fmt(flota)} bicis repartidas por el motor",
                          [("Llenar (70% o más)", ROJO, 40), ("Media (30–70%)", NARANJA, 40), ("Poca (menos de 30%)", AZUL, 40), ("Vacía", GRISC, 20)])
    dif = [e["x06"] - e["x06_prop"] for e in E]
    fig_dif = figura_mapa(E, [ROJO if d > 0 else AZUL if d < 0 else GRISC for d in dif], [5 + abs(d) * 4 for d in dif],
                          "Cambio contra repartir parejo (bicis de más o de menos)",
                          [("Más bicis que parejo", ROJO, 60), ("Menos bicis que parejo", AZUL, 60), ("Igual", GRISC, 15)])

    def curva_est(ax, e, titulo):
        f = np.array(e["curva"]); xm = int(np.argmin(f))
        ax.plot(range(len(f)), f, color=TINTA, lw=2)
        for x, c, t in [(e["x06_prop"], "#9ca3af", f"parejo {e['x06_prop']}"), (e["x06"], ACENTO, f"motor {e['x06']}"), (xm, VERDE, f"mínimo {xm}")]:
            ax.axvline(x, color=c, ls="--" if c == VERDE else "-", lw=1.2)
            ax.scatter([x], [f[x]], color=c, zorder=3, s=25)
        ax.text(.98, .95, f"parejo {e['x06_prop']}: {f[e['x06_prop']]:.1f}\nmotor {e['x06']}: {f[e['x06']]:.1f}\nmínimo {xm}: {f[xm]:.1f}",
                transform=ax.transAxes, ha="right", va="top", fontsize=8, color=TINTA,
                bbox=dict(boxstyle="round", fc="white", ec="#e5e7eb"))
        ax.set_title(titulo, loc="left", fontsize=9.5, fontweight="bold", color=TINTA)
        ax.set_xlabel("bicis al amanecer"); ax.set_ylabel("viajes perdidos en el día")
    salida = max((e for e in E if .25 * e["cap"] < e["x06"] < .75 * e["cap"]), key=lambda e: e["delta"])
    llegada = max((e for e in E if e["x06"] == 0), key=lambda e: e["delta"])
    fig_cur, axs = plt.subplots(1, 2, figsize=(9, 3.3))
    curva_est(axs[0], salida, f"Estación de salida: {nombre_corto(salida['nombre'])}")
    curva_est(axs[1], llegada, f"Estación de llegada: {nombre_corto(llegada['nombre'])}")
    fig_cur.tight_layout()

    fig_flo, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(range(len(PF)), PF, color=TINTA, lw=2)
    ax.axvline(flota, color=ACENTO, lw=1.2); ax.scatter([flota], [PF[flota]], color=ACENTO, zorder=3)
    ax.annotate(f"flota de hoy: {fmt(flota)} bicis → {fmt(PF[flota])} perdidos\ncada bici extra rescata {PF[flota] - PF[flota + 1]:.2f} viajes/día",
                (flota, PF[flota]), xytext=(flota + 400, PF[flota] + 1500), fontsize=8, arrowprops=dict(arrowstyle="-", color=GRIS))
    ax.axvline(nsat, color=GRIS, ls="--", lw=1); ax.text(nsat, PF[0] * .9, f"  saturación\n  {fmt(nsat)} bicis", fontsize=8, color=GRIS, ha="right")
    ax.set_xlabel("bicis repartidas de forma óptima a las 06:00"); ax.set_ylabel("viajes perdidos al día")
    ax.set_title("Cuántos viajes se pierden según el tamaño de la flota", loc="left", fontsize=9.5, fontweight="bold", color=TINTA)
    fig_flo.tight_layout()

    nb = len(D["bloques"]); xs = np.arange(nb); et = [b[:5] for b in D["bloques"]]
    fig_blo, (a1, a2) = plt.subplots(2, 1, figsize=(9, 4.8), sharex=True, gridspec_kw=dict(height_ratios=[3, 2]))
    a1.plot(xs, B["perdida_proporcional"], color="#9ca3af", lw=2, label="repartir parejo")
    a1.plot(xs, B["perdida_optima_06"], color=TINTA, lw=2, label="plan del motor (solo de noche)")
    a1.plot(xs, B["perdida_dinamica"], color=VERDE, lw=2, label="rebalanceo continuo (ideal)")
    a1.set_ylabel("viajes perdidos\npor bloque"); a1.legend(fontsize=8, frameon=False)
    a1.set_title("Durante el día: dónde se concentra la pérdida (bloques de 33 min)", loc="left", fontsize=9.5, fontweight="bold", color=TINTA)
    for a in (a1, a2):
        a.axvspan(3 - .5, 7 - .5, color=ACENTO, alpha=.08); a.axvspan(20 - .5, nb - .5, color=ACENTO, alpha=.08)
    a2.bar(xs, B["bicis_a_mover"], color=ACENTO, alpha=.8)
    a2.set_ylabel("bicis a mover\npara llegar a la meta")
    a2.set_xticks(xs[::2]); a2.set_xticklabels(et[::2], rotation=0, fontsize=7.5)
    fig_blo.tight_layout()

    # ---------- cifras
    llen = [e for e in E if e["llen"] >= .7]; med = [e for e in E if .3 <= e["llen"] < .7]
    poca = [e for e in E if 0 < e["llen"] < .3]; vac = [e for e in E if e["x06"] == 0]
    tot_res = T["perdida_proporcional"] - T["perdida_optima_06"]
    pico = slice(3, 7); tarde = slice(20, nb)
    perd_pico = sum(B["perdida_optima_06"][pico]); perd_tarde = sum(B["perdida_optima_06"][tarde])
    k_pico = int(np.argmax(B["perdida_optima_06"]))

    # ---------- historia
    h = []
    h.append(Paragraph("Reparto de bicis Ecobici", H1))
    h.append(Paragraph(f"Plan para un día hábil de 06:00 a 19:00 · {D['dias_habiles']} días hábiles de julio a septiembre de 2026 "
                       f"(sin el feriado {', '.join(D['feriados_excluidos'])}) · {fmt(D['viajes_en_ventana'])} viajes · {len(E)} estaciones", SUB))
    h.append(kpis([(f"−{fmt(tot_res)}", f"viajes perdidos al día (−{100 * tot_res / T['perdida_proporcional']:.0f}%) con la misma flota"),
                   (fmt(flota), "bicis en estaciones a las 06:00 (supuesto)"),
                   (f"{len(llen)} / {len(vac)}", "estaciones a llenar (70%+) / a dejar vacías"),
                   (f"{100 * (perd_pico + perd_tarde) / T['perdida_optima_06']:.0f}%", "de la pérdida cae en 2 picos (07:39–09:51 y 17:00–19:00)")]))
    h.append(Spacer(1, 8))
    h.append(Paragraph("En una línea", H2))
    h.append(Paragraph(f"<b>No conviene repartir las bicis parejo.</b> Con las mismas {fmt(flota)} bicis, el motor llena al tope los puntos donde la gente "
                       f"<b>sale</b> en la mañana (sobre todo Buenavista: Jesús García y Luis Donaldo Colosio) y deja con espacio los puntos donde la gente "
                       f"<b>llega</b> a trabajar (Reforma y Polanco). Según el modelo, eso baja los viajes perdidos de <b>{fmt(T['perdida_proporcional'])}</b> a "
                       f"<b>{fmt(T['perdida_optima_06'])}</b> al día. Una simulación de la red que re-juega {D['dias_habiles']} días reales da una mejora parecida: "
                       f"de {fmt(perd_sim(S['parejo']))} a {fmt(perd_sim(S['plan_mezcla']))} (viajes servidos: {fmt(S['parejo']['viajes_servidos'])} → "
                       f"{fmt(S['plan_mezcla']['viajes_servidos'])}).", P))
    h.append(Paragraph("Qué hacer", H2))
    for t in [
        f"<b>De noche:</b> amanecer con el reparto del motor (mapa de la página 2 y anexo con las {len(E)} estaciones). {len(llen)} estaciones al 70% o más "
        f"concentran {fmt(sum(e['x06'] for e in llen))} bicis ({100 * sum(e['x06'] for e in llen) / flota:.0f}% de la flota); {len(vac)} amanecen vacías.",
        f"<b>Pico de la mañana (07:39–09:51):</b> camiones en lanzadera de Reforma/Polanco hacia Buenavista. Ahí se pierden {fmt(perd_pico)} viajes aun con el plan; "
        f"el bloque más crítico es {D['bloques'][k_pico]}.",
        f"<b>Antes de las 17:00:</b> segundo acomodo; de 17:00 a 19:00 se pierden {fmt(perd_tarde)} viajes porque el plan nocturno está hecho para la mañana.",
        f"<b>Flota:</b> cada bici extra rescata {PF[flota] - PF[flota + 1]:.2f} viajes al día; después de ~{fmt(nsat)} bicis ya no ayuda y lo que falta son muelles.",
        "<b>Validar antes de invertir:</b> los viajes perdidos no se registran en ningún lado; son estimaciones. Siguiente paso: guardar el feed en vivo "
        "varias semanas (backtest) y un piloto por zonas.",
    ]:
        h.append(Paragraph("• " + t, P))
    h.append(Paragraph("Resumen de resultados", H2))
    h.append(tabla([
        ["Reparto a las 06:00", "Viajes perdidos al día (modelo)", "Simulación con días reales", "Viajes servidos (simulación)"],
        ["Repartir parejo (mismo % en todas)", fmt(T["perdida_proporcional"]), fmt(perd_sim(S["parejo"])), fmt(S["parejo"]["viajes_servidos"])],
        ["<b>Plan del motor</b>", f"<b>{fmt(T['perdida_optima_06'])}</b>", f"<b>{fmt(perd_sim(S['plan_mezcla']))}</b>", f"<b>{fmt(S['plan_mezcla']['viajes_servidos'])}</b>"],
        ["+ rebalanceo continuo (cota ideal)", fmt(T["perdida_dinamica_perfecta"]), "—", "—"],
    ], [6.4, 4, 4, 4], alinear_der=(1, 2, 3)))
    h.append(Paragraph("La simulación da niveles más altos que el modelo porque acopla estaciones y no incluye viajes en curso al inicio; lo comparable es la diferencia. "
                       "El rebalanceo continuo es un techo teórico: exigiría mover cientos o miles de bicis cada 33 minutos.", NOTA))
    h.append(PageBreak())

    h.append(Paragraph("1. Cómo amanecer: el reparto", H2))
    h.append(a_imagen(fig_rep, 12.6))
    h.append(tabla([
        ["Tipo de estación", "Estaciones", "Bicis", "% de la flota", "Por qué"],
        ["Llenar (70% o más)", len(llen), fmt(sum(e["x06"] for e in llen)), f"{100 * sum(e['x06'] for e in llen) / flota:.0f}%", "salen muchas bicis en la mañana"],
        ["Media (30–70%)", len(med), fmt(sum(e["x06"] for e in med)), f"{100 * sum(e['x06'] for e in med) / flota:.0f}%", "salen y llegan parecido"],
        ["Poca (menos de 30%)", len(poca), fmt(sum(e["x06"] for e in poca)), f"{100 * sum(e['x06'] for e in poca) / flota:.0f}%", "poca demanda o llegan más de las que salen"],
        ["Vacía", len(vac), "0", "0%", "llegan bicis: dejar muelles libres"],
    ], [3.6, 2.2, 2, 2.4, 8.2], alinear_der=(1, 2, 3)))
    h.append(PageBreak())

    h.append(Paragraph("2. Dónde cambia contra repartir parejo", H2))
    h.append(a_imagen(fig_dif, 11.5))
    mas = sorted(E, key=lambda e: -(e["x06"] - e["x06_prop"]))[:12]
    menos = sorted(E, key=lambda e: (e["x06"] - e["x06_prop"]))[:12]
    filas = [["Estación", "Muelles", "Parejo", "Motor", "Viajes/día"]]
    filas += [[f"{clave(e['nombre'])} {nombre_corto(e['nombre'])}", e["cap"], e["x06_prop"], f"<b>{e['x06']}</b>", f"{e['delta']:+.1f}"] for e in mas]
    t1 = tabla(filas, [10.4, 2.0, 2.0, 2.0, 2.0], alinear_der=(1, 2, 3, 4))
    filas = [["Estación", "Muelles", "Parejo", "Motor", "Viajes/día"]]
    filas += [[f"{clave(e['nombre'])} {nombre_corto(e['nombre'])}", e["cap"], e["x06_prop"], f"<b>{e['x06']}</b>", f"{e['delta']:+.1f}"] for e in menos]
    t2 = tabla(filas, [10.4, 2.0, 2.0, 2.0, 2.0], alinear_der=(1, 2, 3, 4))
    h.append(KeepTogether([Paragraph("Las 12 estaciones que más ganan bicis", H2), t1]))
    h.append(KeepTogether([Paragraph("Las 12 que más ceden bicis", H2), t2]))
    h.append(Paragraph("Viajes/día = viajes perdidos que se evitan (+) o que se agregan (−) en esa estación contra repartir parejo. "
                       "Algunas estaciones pierden un poco al ceder bicis; esas bicis rescatan mucho más en otra parte.", NOTA))

    h.append(Paragraph("3. Cómo decide el motor", H2))
    h.append(Paragraph("<b>Paso 1. Una curva por estación.</b> Con las tasas de retiros y devoluciones de cada estación en bloques de 33 minutos, una cadena de "
                       "Markov de tiempo continuo calcula cuántos viajes perdería en el día según con cuántas bicis amanezca. Con pocas bicis, la gente "
                       "llega y no hay bici; con demasiadas, llega a devolver y no hay muelle.", P))
    h.append(a_imagen(fig_cur, 18))
    h.append(Paragraph(f"<b>Paso 2. Bici por bici.</b> El motor reparte las {fmt(flota)} bicis una por una: cada bici va a la estación donde rescata más viajes en ese "
                       "momento. Como cada curva es convexa (cada bici adicional rescata menos que la anterior), este reparto es el óptimo. Por eso a una estación "
                       "no siempre le toca su mínimo: la flota no alcanza y las bicis van primero a donde valen más.", P))
    h.append(a_imagen(fig_flo, 18))

    h.append(KeepTogether([Paragraph("4. Durante el día", H2), a_imagen(fig_blo, 18)]))
    h.append(Paragraph(f"El plan de la noche aguanta la mañana temprano, pero en el pico ({D['bloques'][3][:5]}–{D['bloques'][6][-5:]}) las estaciones de salida se "
                       "vacían y las de llegada se llenan. Las barras muestran cuántas bicis habría que mover en cada bloque para llegar a la meta (mejor reparto "
                       "para las siguientes ~2 horas). Es imposible moverlas todas; sirven para decidir dónde poner los camiones.", P))
    pk = 5
    lle = sorted(E, key=lambda e: -(e["meta"][pk] - e["E"][pk]))[:8]
    sac = sorted(E, key=lambda e: (e["meta"][pk] - e["E"][pk]))[:8]
    filas = [["Llevar bicis a", "Esperadas", "Meta"]] + [[nombre_corto(e["nombre"]), f"{e['E'][pk]:.0f}", e["meta"][pk]] for e in lle]
    filas2 = [["Sacar bicis de", "Esperadas", "Meta"]] + [[nombre_corto(e["nombre"]), f"{e['E'][pk]:.0f}", e["meta"][pk]] for e in sac]
    titulo_pk = Paragraph(f"Bloque {D['bloques'][pk]}: a dónde mover", st("h3", f="DVB", s=10, l=14, spaceBefore=6, spaceAfter=4))
    lado = Table([[tabla(filas, [5.6, 1.9, 1.4], alinear_der=(1, 2)), tabla(filas2, [5.6, 1.9, 1.4], alinear_der=(1, 2))]], colWidths=[9.2 * cm, 9.2 * cm])
    lado.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    h.append(KeepTogether([titulo_pk, lado]))

    h.append(Paragraph("5. Cómo se hizo y qué tan confiable es", H2))
    for t in [
        f"<b>Datos:</b> viajes públicos de Ecobici de julio a septiembre de 2026, lunes a viernes sin feriados ({D['dias_habiles']} días), de 06:00 a 19:00; "
        "estado y capacidad de estaciones del feed público GBFS (muelles útiles del 2026-10-25).",
        f"<b>Demanda:</b> Poisson con tasa aleatoria por día. Los días reales se agrupan en {len(D['escenarios'])} escenarios (septiembre alto, lunes bajo, "
        "tardes bajas, etc.) y cada estación se resuelve en cada escenario. Da el mismo plan que Poisson puro (difieren "
        f"{T['bicis_que_cambian']} bicis): la variabilidad que falta es local de cada estación.",
        "<b>Validación:</b> simulación de toda la red con viajes reales re-muestreados (origen, destino y duración), 30 réplicas.",
        "<b>Límites:</b> (1) los viajes perdidos no se observan, son estimaciones; (2) cuando una estación está vacía nadie registra la demanda, así que "
        "se subestima; (3) parte de quien encuentra vacía camina a otra estación, así que se sobreestima; (4) el modelo por estación no conserva bicis "
        "(a mediodía \"infla\" la flota ~12%); (5) la flota de 4,759 es un supuesto; (6) no modela rutas ni costo de camiones.",
    ]:
        h.append(Paragraph("• " + t, P))

    h.append(PageBreak())
    h.append(Paragraph(f"Anexo. Reparto a las 06:00 por estación ({len(E)} estaciones)", H2))
    h.append(Paragraph("Ordenado por número de estación. Parejo = mismo % de llenado en todas. Motor = reparto óptimo. Viajes/día = viajes perdidos que se evitan (+) "
                       "o se agregan (−) contra parejo.", NOTA))
    filas = [["Clave", "Estación", "Muelles", "Parejo", "Motor", "Llenado", "Viajes/día"]]
    num = lambda e: int(re.match(r"\d+", clave(e["nombre"]) or "0").group()) if re.match(r"\d+", clave(e["nombre"]) or "") else 0
    for e in sorted(E, key=num):
        filas.append([clave(e["nombre"]), nombre_corto(e["nombre"]), e["cap"], e["x06_prop"], f"<b>{e['x06']}</b>", f"{100 * e['llen']:.0f}%", f"{e['delta']:+.1f}"])
    h.append(tabla(filas, [1.6, 8.6, 1.7, 1.5, 1.5, 1.6, 1.9], alinear_der=(2, 3, 4, 5, 6)))

    destino = RAIZ / "salida" / "reparto_ecobici.pdf"
    doc = SimpleDocTemplate(str(destino), pagesize=letter, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.4 * cm, bottomMargin=1.6 * cm,
                            title="Reparto de bicis Ecobici", author="Prototipo de mentoría Chilangotón 2026", subject="Plan de reparto día hábil")
    doc.build(h, onFirstPage=pie, onLaterPages=pie)
    print(destino, destino.stat().st_size)


if __name__ == "__main__":
    main()
