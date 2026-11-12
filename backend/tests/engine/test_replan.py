from dataclasses import replace

import pytest

from engine import alns, greedy
from engine.evaluation import evaluate, schedule
from engine.instance import Params, Visit
from engine.replan import (cronograma, estado_em, estender, inserir_regra, objetivo_dia, planejar_do_zero,
                           reotimizar, subproblema, viavel)
from tests.engine.test_greedy import random_instance

P = Params(beta=30, gamma=20, iterations=120, time_limit_s=60, seeds=[0, 1])


def nova_visita(s: float = 15, grave: bool = False) -> Visit:
    return Visit(id=999, node=999, s=s, w=3, P=7, d=3, tw=(0, 240), urgent=True, grave=grave)


def cenario(seed: int, s: float = 15, dist: float = 4.0):
    """Instância, plano do dia, instância estendida com a nova urgência (a `dist` min de todos) e o índice dela."""
    inst = random_instance(seed)
    plano = alns.solve(inst, P, seed=0)
    ext = estender(inst, nova_visita(s), [dist] * inst.n, [dist] * inst.n)
    return inst, plano, ext, inst.n


def test_estado_antes_da_primeira_parada():
    inst, plano, *_ = cenario(1)
    e = estado_em(inst, plano.route, 0.5)
    assert e.prefix == [0] and e.clock == pytest.approx(0.5) and e.walk == 0


def test_estado_durante_a_visita_parte_do_fim_dela():
    inst, plano, *_ = cenario(1)
    stops, _, _ = schedule(inst, plano.route)
    tau = stops[1].start + 0.5 * (stops[1].end - stops[1].start)
    e = estado_em(inst, plano.route, tau)
    assert e.prefix == plano.route[:3]
    assert e.clock == pytest.approx(stops[1].end)
    assert e.walk == pytest.approx(stops[0].walk_from_prev + stops[1].walk_from_prev)


def test_estado_caminhando_conta_o_tempo_gasto():
    inst, plano, *_ = cenario(1)
    stops, _, _ = schedule(inst, plano.route)
    tau = stops[0].end + 0.5 * (stops[1].arrival - stops[0].end)  # entre as paradas 1 e 2
    assert stops[0].end < tau < stops[1].arrival
    e = estado_em(inst, plano.route, tau)
    assert e.prefix == plano.route[:2] and e.clock == pytest.approx(tau)


def test_estado_apos_o_retorno_levanta_erro():
    inst, plano, *_ = cenario(1)
    with pytest.raises(ValueError):
        estado_em(inst, plano.route, plano.H + 1)
    with pytest.raises(ValueError):
        estado_em(inst, plano.route, -1)


@pytest.mark.parametrize("seed", range(6))
def test_regra_poe_a_urgencia_primeiro_e_fica_viavel(seed):
    inst, plano, ext, novo = cenario(seed)
    e = estado_em(inst, plano.route, 60)
    restante = plano.route[len(e.prefix):-1]
    a = inserir_regra(ext, P, e, restante, novo)
    assert a.atendida and a.suffix[0] == novo
    assert viavel(ext, e, a.suffix)
    assert set(a.suffix) | set(a.retiradas) == set(restante) | {novo}
    assert a.objetivo < float("inf")


def test_regra_retira_primeiro_as_de_menor_penalidade_e_nunca_urgentes():
    houve_retirada = houve_recusa = False
    for seed in range(6):
        for s in (60, 90, 120, 160):
            inst, plano, ext, novo = cenario(seed, s=s)
            e = estado_em(inst, plano.route, 90)
            restante = plano.route[len(e.prefix):-1]
            a = inserir_regra(ext, P, e, restante, novo)
            pens = [ext.visits[i].penalty for i in a.retiradas]
            assert pens == sorted(pens)
            assert not any(ext.visits[i].urgent for i in a.retiradas)
            if a.atendida:
                assert viavel(ext, e, a.suffix)
            else:
                houve_recusa = True
            houve_retirada |= bool(a.retiradas)
    assert houve_retirada and houve_recusa


def test_regra_so_retira_graves_quando_o_problema_e_o_teto():
    # sem espaço no teto de graves: K = 0 e a nova urgência é grave → só visitas graves podem sair
    inst, plano, _, _ = cenario(2)
    inst = replace(inst, K=0)
    ext = estender(inst, nova_visita(grave=True), [4.0] * inst.n, [4.0] * inst.n)
    e = estado_em(inst, plano.route, 30)
    restante = plano.route[len(e.prefix):-1]
    a = inserir_regra(ext, P, e, restante, inst.n)
    assert all(ext.visits[i].grave for i in a.retiradas)
    if a.atendida:
        assert viavel(ext, e, a.suffix)


@pytest.mark.parametrize("seed", range(4))
def test_subproblema_tem_o_mesmo_objetivo_do_dia(seed):
    inst, plano, ext, novo = cenario(seed)
    e = estado_em(inst, plano.route, 75)
    restante = plano.route[len(e.prefix):-1]
    a = inserir_regra(ext, P, e, restante, novo)
    sub, ids = subproblema(ext, e)
    mapa = {orig: k for k, orig in enumerate(ids)}
    rota_sub = [0, *(mapa[i] for i in a.suffix), 0]
    ev = evaluate(sub, P, rota_sub)
    assert e.walk + ev.objective == pytest.approx(a.objetivo)
    assert e.clock + ev.H == pytest.approx(a.H)
    assert not ev.violations


@pytest.mark.parametrize("seed", range(4))
def test_reotimizar_nunca_pior_que_a_regra_e_mantem_a_urgencia(seed):
    inst, plano, ext, novo = cenario(seed)
    e = estado_em(inst, plano.route, 75)
    restante = plano.route[len(e.prefix):-1]
    a = inserir_regra(ext, P, e, restante, novo)
    b = reotimizar(ext, P, e, a, restante)
    assert b.objetivo <= a.objetivo + 1e-9
    assert novo in b.suffix and viavel(ext, e, b.suffix)
    assert not set(b.suffix) & set(e.prefix)
    urgentes = [i for i in b.suffix if ext.visits[i].urgent]
    assert b.suffix[:len(urgentes)] == urgentes  # urgências primeiro também no restante


def test_referencia_inclui_a_urgencia_desde_o_inicio():
    _, _, ext, novo = cenario(3)
    c = planejar_do_zero(ext, P)
    assert novo in c.suffix and c.objetivo < float("inf")


def test_cronograma_a_partir_de_um_instante():
    inst, plano, *_ = cenario(1)
    H, walk, ok = cronograma(inst, plano.route, 0.0)
    assert ok and H == pytest.approx(plano.H) and walk == pytest.approx(plano.walk)
    H2, _, _ = cronograma(inst, plano.route, 10.0)
    assert H2 >= H + 10 - 1e-9 or H2 > H  # partir mais tarde nunca volta mais cedo
    r = objetivo_dia(inst, P, estado_em(inst, plano.route, 0.5), plano.route[1:-1])
    assert r.objetivo == pytest.approx(plano.objective, abs=0.6 * P.gamma)  # difere só pelo 0,5 min de atraso na partida
