"""
Urgência que surge durante o dia (§6.4 da proposta): replanejamento do restante da jornada.

O plano do dia já está em andamento. No minuto `tau` uma casa vira urgente. O que o ACS já visitou não
muda; o que falta é replanejado. Duas estratégias, mais uma referência:

  A. regra da proposta (`inserir_regra`): a nova urgência entra logo depois da visita em andamento; se a
     jornada estourar (ou uma janela fechar, ou o teto de graves), retiram-se do restante as visitas não
     urgentes de menor penalidade, uma a uma, até o roteiro voltar a caber. Custo desprezível.
  B. regra + ALNS no restante (`reotimizar`): parte da rota da regra A e roda a ALNS só sobre o que falta
     do dia, com o que já foi feito travado. Pode trazer de volta visitas que a regra A retirou.
  C. referência (`planejar_do_zero`): replaneja o dia inteiro como se a urgência fosse conhecida desde o
     início. Não é aplicável na prática (o ACS já andou); serve de limite para medir o que A e B perdem.

Modelo da posição do ACS (aproximação): a parada em andamento (ou a última concluída) é o ponto de
partida do replanejamento. Se o ACS está caminhando entre duas paradas, o tempo já gasto na caminhada
conta como gasto, e ele é tratado como estando na parada anterior. Não há posição contínua no mapa.

Todas as estratégias são comparadas pelo mesmo objetivo do dia (`objetivo_dia`): caminhada total +
β·penalidade residual + γ·excesso de jornada, o de evaluation.py.
"""

from dataclasses import dataclass, field, replace
from time import perf_counter

import numpy as np

from . import alns, greedy
from .evaluation import schedule
from .instance import Instance, Params, Visit

INF = float("inf")


@dataclass
class Estado:
    """Onde o ACS está quando a urgência surge."""
    prefix: list[int]       # rota já comprometida, de 0 (UBS) até a posição atual (última)
    clock: float            # instante (min desde o início da jornada) em que ele pode partir da posição atual
    walk: float             # caminhada já feita (min)

    @property
    def pos(self) -> int:
        return self.prefix[-1]


@dataclass
class Resultado:
    estrategia: str
    atendida: bool                                       # a nova urgência cabe no dia
    suffix: list[int] = field(default_factory=list)      # visitas que faltam (sem a posição atual nem a UBS)
    retiradas: list[int] = field(default_factory=list)   # visitas do plano original que saíram
    objetivo: float = INF
    caminhada: float = 0.0
    residual: float = 0.0
    excesso: float = 0.0
    H: float = 0.0
    runtime_s: float = 0.0


# ---------- instância e estado ----------
def estender(inst: Instance, nova: Visit, t_para_nova, t_da_nova) -> Instance:
    """
    Instance com a nova visita no índice `inst.n`. `t_para_nova[i]` = tempo a pé de visits[i] até a nova;
    `t_da_nova[i]` = da nova até visits[i].
    """
    n = inst.n
    t = np.zeros((n + 1, n + 1))
    t[:n, :n] = inst.t
    t[:n, n] = t_para_nova
    t[n, :n] = t_da_nova
    return Instance(visits=[*inst.visits, nova], t=t, T=inst.T, Tmax=inst.Tmax, K=inst.K)


def estender_por_no(inst: Instance, nova: Visit, nodes: np.ndarray, t: np.ndarray) -> Instance:
    """Como `estender`, com os tempos tirados da matriz da microárea (matrix.build_matrix / io.ensure_matrix)."""
    pos = {int(n): i for i, n in enumerate(nodes)}
    idx = [pos[v.node] for v in inst.visits]
    j = pos[nova.node]
    return estender(inst, nova, t[idx, j], t[j, idx])


def estado_em(inst: Instance, route: list[int], tau: float) -> Estado:
    """Estado do ACS no minuto `tau` do plano `route`: comprometido com toda parada a que já chegou."""
    if tau < 0:
        raise ValueError("tau negativo")
    stops, H, _ = schedule(inst, route)
    if tau >= H:
        raise ValueError(f"o ACS já voltou à UBS no minuto {H:.1f}")
    k = sum(1 for s in stops if s.arrival <= tau + 1e-9)
    clock = max(tau, stops[k - 1].end) if k else tau
    return Estado(prefix=list(route[:1 + k]), clock=clock, walk=sum(s.walk_from_prev for s in stops[:k]))


# ---------- cronograma e viabilidade do restante ----------
def cronograma(inst: Instance, seq: list[int], clock0: float) -> tuple[float, float, bool]:
    """
    (H, caminhada, janelas respeitadas) de `seq` = [posição atual, v1, ..., UBS], partindo de seq[0] no
    instante `clock0`. H é o retorno à UBS (min desde o início da jornada).
    """
    t, vis = inst.t, inst.visits
    clock, walk, prev = clock0, 0.0, seq[0]
    for i in seq[1:-1]:
        leg = t[prev, i]
        a, b = vis[i].tw
        start = clock + leg
        if start < a:
            start = a
        elif start > b + 1e-9:
            return INF, walk + leg, False
        clock = start + vis[i].s
        walk += leg
        prev = i
    back = t[prev, seq[-1]]
    return float(clock + back), float(walk + back), True


def _graves_ok(inst: Instance, ids: list[int]) -> bool:
    """Mesma regra de evaluation.violations: urgentes graves são obrigatórias mesmo acima de K."""
    graves = [i for i in ids if inst.visits[i].grave]
    return len(graves) <= max(inst.K, sum(inst.visits[i].urgent for i in graves))


def viavel(inst: Instance, estado: Estado, suffix: list[int], H_max: float | None = None) -> bool:
    """Janelas, graves e retorno até `H_max` (padrão: Tmax)."""
    H, _, ok = cronograma(inst, [estado.pos, *suffix, 0], estado.clock)
    return ok and H <= (inst.Tmax if H_max is None else H_max) + 1e-9 and _graves_ok(inst, estado.prefix[1:] + suffix)


def objetivo_dia(inst: Instance, params: Params, estado: Estado, suffix: list[int]) -> Resultado:
    """Objetivo do dia inteiro (feito + restante) para comparar estratégias; `atendida` fica por conta do chamador."""
    H, walk, ok = cronograma(inst, [estado.pos, *suffix, 0], estado.clock)
    feitas = set(estado.prefix[1:]) | set(suffix)
    residual = sum(inst.visits[i].penalty for i in inst.candidates if i not in feitas)
    excesso = max(0.0, H - inst.T) if ok else INF
    obj = estado.walk + walk + params.beta * residual + params.gamma * excesso if ok else INF
    return Resultado(estrategia="", atendida=True, suffix=list(suffix), objetivo=obj, caminhada=estado.walk + walk,
                     residual=residual, excesso=excesso, H=H)


# ---------- A: regra da proposta ----------
def inserir_regra(inst: Instance, params: Params, estado: Estado, suffix: list[int], nova: int,
                  H_max: float | None = None, nome: str = "A_regra") -> Resultado:
    """
    Insere `nova` (índice em `inst`, já marcada como urgente) logo depois da posição atual. Enquanto o
    restante não couber (janela, Tmax ou teto de graves), retira a visita não urgente de menor penalidade
    (se o problema é o teto de graves, só entre as graves). `atendida` = False se mesmo sem nenhuma visita
    não urgente a nova urgência não cabe. `H_max` é até quando o ACS pode voltar (padrão: Tmax, como na proposta).
    """
    start = perf_counter()
    cand, retiradas = [nova, *suffix], []
    while True:
        graves_ok = _graves_ok(inst, estado.prefix[1:] + cand)
        if viavel(inst, estado, cand, H_max):
            r = objetivo_dia(inst, params, estado, cand)
            r.estrategia, r.retiradas, r.runtime_s = nome, retiradas, perf_counter() - start
            return r
        removiveis = [i for i in cand if not inst.visits[i].urgent and (inst.visits[i].grave or graves_ok)]
        if not removiveis:
            return Resultado(estrategia=nome, atendida=False, retiradas=retiradas, runtime_s=perf_counter() - start)
        pior = min(removiveis, key=lambda i: (inst.visits[i].penalty, -i))
        cand.remove(pior)
        retiradas.append(pior)


def inserir_regra_sem_extra(inst: Instance, params: Params, estado: Estado, suffix: list[int], nova: int) -> Resultado:
    """
    Variante da regra A: retira visitas até o ACS voltar dentro da jornada nominal T (sem hora extra). Se nem
    assim cabe (ou o ACS já passou de T), cai na regra da proposta, que aceita até Tmax.
    """
    r = inserir_regra(inst, params, estado, suffix, nova, H_max=inst.T, nome="A2_sem_extra")
    return r if r.atendida else inserir_regra(inst, params, estado, suffix, nova, nome="A2_sem_extra")


# ---------- B: ALNS no restante do dia ----------
def subproblema(inst: Instance, estado: Estado) -> tuple[Instance, list[int]]:
    """
    Instance do que falta do dia. O índice 0 é a posição atual do ACS: sai dele (t[0, j]) e volta à UBS
    (t[j, 0]), e os tempos são contados a partir de `estado.clock`. Entram todas as candidatas ainda não
    visitadas (o restante do plano, a nova urgência e as que ficaram de fora). Devolve a instância e o
    índice original de cada visita (ids[0] = posição atual).
    """
    c0, pos = estado.clock, estado.pos
    feitas = set(estado.prefix[1:])
    pool = [i for i in inst.candidates if i not in feitas]
    ids = [pos, *pool]
    m = len(ids)
    t = np.zeros((m, m))
    t[1:, 1:] = inst.t[np.ix_(pool, pool)]
    t[0, 1:] = inst.t[pos, pool]            # da posição atual a cada visita
    t[1:, 0] = inst.t[pool, 0]              # de cada visita à UBS
    t[0, 0] = inst.t[pos, 0]                # rota vazia: da posição atual direto à UBS
    visits = [Visit(id=-2, node=inst.visits[pos].node, s=0, w=0, P=0, d=0, tw=(0, inst.Tmax - c0))]
    for i in pool:
        v = inst.visits[i]
        visits.append(replace(v, tw=(max(0.0, v.tw[0] - c0), v.tw[1] - c0)))
    graves_feitos = sum(inst.visits[i].grave for i in estado.prefix[1:])
    sub = Instance(visits=visits, t=t, T=inst.T - c0, Tmax=inst.Tmax - c0, K=max(0, inst.K - graves_feitos))
    return sub, ids


def reotimizar(inst: Instance, params: Params, estado: Estado, partida: Resultado,
               plano_original: list[int]) -> Resultado:
    """ALNS (sementes de `params.seeds`) sobre o restante do dia, partindo da rota da regra A (`partida`)."""
    start = perf_counter()
    sub, ids = subproblema(inst, estado)
    mapa = {orig: k for k, orig in enumerate(ids)}
    inicial = [0, *(mapa[i] for i in partida.suffix), 0]
    melhor = min((alns.solve(sub, params, initial=inicial, seed=s) for s in params.seeds),
                 key=lambda s: (not s.feasible, s.objective))
    suffix = [ids[k] for k in melhor.route[1:-1]]
    r = objetivo_dia(inst, params, estado, suffix)
    r.estrategia = "B_regra+ALNS"
    r.retiradas = [i for i in plano_original if i not in suffix and i not in estado.prefix]
    r.runtime_s = perf_counter() - start
    return r


# ---------- C: referência ----------
def planejar_do_zero(inst: Instance, params: Params) -> Resultado:
    """Plano do dia inteiro com a urgência já conhecida no início (limite, não aplicável na prática)."""
    start = perf_counter()
    sol = alns.solve_multi_seed(inst, params)
    r = objetivo_dia(inst, params, Estado(prefix=[0], clock=0.0, walk=0.0), sol.route[1:-1])
    r.estrategia, r.runtime_s = "C_referencia", perf_counter() - start
    return r


# ---------- uso: aviso à gestão ----------
@dataclass
class Replanejamento:
    """Resposta de `replanejar`: o novo restante do dia e o que a gestão precisa saber."""
    status: str                                           # "atendida" ou "urgencia_excedente"
    resultado: Resultado                                  # restante do dia escolhido (vazio se excedente)
    retiradas: dict[int, str] = field(default_factory=dict)  # visita → motivo (`retirada_por_urgencia`)
    minutos_faltando: float = 0.0                         # se excedente: quanto passa de Tmax mesmo só com urgências
    mensagem: str = ""


def replanejar(inst: Instance, params: Params, estado: Estado, restante: list[int], nova: int,
               reotimizar_restante: bool = True) -> Replanejamento:
    """
    Recomendação para uma urgência que surgiu: regra da proposta e, se `reotimizar_restante`, ALNS no restante
    do dia partindo dela (nunca pior). Se a nova urgência não cabe nem sem nenhuma visita não urgente, devolve
    `urgencia_excedente` para a gestão decidir (acionar outro agente, ou abrir mão de outra urgência).
    """
    a = inserir_regra(inst, params, estado, restante, nova)
    if not a.atendida:
        so_urgentes = [nova, *(i for i in restante if inst.visits[i].urgent)]
        H, _, _ = cronograma(inst, [estado.pos, *so_urgentes, 0], estado.clock)
        falta = max(0.0, H - inst.Tmax) if H < INF else INF
        return Replanejamento(
            status="urgencia_excedente", resultado=a, minutos_faltando=falta,
            mensagem="A nova urgência não cabe na jornada nem retirando todas as visitas não urgentes"
                     + (f" (faltam {falta:.0f} min)." if falta < INF else " (janela de horário incompatível).")
                     + " Acionar a gestão.")
    r = reotimizar(inst, params, estado, a, restante) if reotimizar_restante else a
    ret = {i: "retirada_por_urgencia" for i in r.retiradas}
    return Replanejamento(status="atendida", resultado=r, retiradas=ret,
                          mensagem=f"Urgência incluída; {len(ret)} visita(s) não urgente(s) retirada(s) do restante do dia.")
