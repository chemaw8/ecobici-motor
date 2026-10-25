"""Pruebas del núcleo CTMC contra soluciones analíticas (Poisson)."""
import numpy as np
from scipy.stats import poisson

from motor import demanda_perdida, demanda_perdida_mezcla


def _esperanza_exceso(media, umbral):
    """E[max(N - umbral, 0)] con N ~ Poisson(media)."""
    n = np.arange(0, 400)
    return float(np.sum(np.maximum(n - umbral, 0) * poisson.pmf(n, media)))


def test_solo_retiros_coincide_con_poisson():
    cap, mu, horas = 10, 3.0, 2
    perd_ret, perd_dev = demanda_perdida(cap, lam=np.zeros(horas), mu=np.full(horas, mu))
    for x in range(cap + 1):
        assert abs(perd_ret[x] - _esperanza_exceso(mu * horas, x)) < 1e-6
    assert np.allclose(perd_dev, 0)


def test_solo_devoluciones_coincide_con_poisson():
    cap, lam, horas = 8, 2.5, 3
    perd_ret, perd_dev = demanda_perdida(cap, lam=np.full(horas, lam), mu=np.zeros(horas))
    for x in range(cap + 1):
        assert abs(perd_dev[x] - _esperanza_exceso(lam * horas, cap - x)) < 1e-6
    assert np.allclose(perd_ret, 0)


def test_bloques_de_duracion_variable():
    # Dos bloques de 33 y 21 min con la misma tasa = un bloque de 54 min.
    cap, lam, mu = 12, 3.0, 4.0
    r1, d1 = demanda_perdida(cap, lam=[lam, lam], mu=[mu, mu], tau=[33 / 60, 21 / 60])
    r2, d2 = demanda_perdida(cap, lam=[lam], mu=[mu], tau=54 / 60)
    assert np.allclose(r1, r2, atol=1e-9) and np.allclose(d1, d2, atol=1e-9)
    # y con solo retiros coincide con Poisson(mu * 54/60)
    r3, _ = demanda_perdida(cap, lam=[0, 0], mu=[mu, mu], tau=[33 / 60, 21 / 60])
    assert abs(r3[4] - _esperanza_exceso(mu * 54 / 60, 4)) < 1e-6


def test_mezcla_es_promedio_ponderado_y_convexa():
    cap, lam, mu, tau = 15, np.array([2.0, 5.0]), np.array([4.0, 1.0]), [0.55, 0.55]
    perfiles, pesos = np.array([[0.6, 0.7], [1.3, 1.2]]), np.array([0.25, 0.75])
    ret, dev = demanda_perdida_mezcla(cap, lam, mu, tau, perfiles, pesos)
    esperado = sum(w * np.array(demanda_perdida(cap, lam * th, mu * th, tau)) for th, w in zip(perfiles, pesos))
    assert np.allclose(ret, esperado[0]) and np.allclose(dev, esperado[1])
    assert np.all(np.diff(ret + dev, 2) > -1e-9)
    # un solo escenario con multiplicador 1 = Poisson puro
    r1, d1 = demanda_perdida_mezcla(cap, lam, mu, tau, np.ones((1, 2)), np.ones(1))
    r0, d0 = demanda_perdida(cap, lam, mu, tau)
    assert np.allclose(r1, r0) and np.allclose(d1, d0)


def test_tasas_por_hora_cambian_el_optimo():
    # Mañana se vacía (retiros), tarde se llena: el óptimo inicial es interior.
    cap = 20
    lam = np.array([0.5, 0.5, 6.0, 6.0])
    mu = np.array([6.0, 6.0, 0.5, 0.5])
    perd_ret, perd_dev = demanda_perdida(cap, lam, mu)
    total = perd_ret + perd_dev
    assert 0 < int(np.argmin(total)) < cap
    # Convexidad (Raviv & Kolka 2013): segundas diferencias no negativas.
    assert np.all(np.diff(total, 2) > -1e-9)
