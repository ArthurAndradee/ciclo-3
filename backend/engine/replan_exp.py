"""
Experimento da urgência no meio do dia (hipótese H4): quanto a regra simples (A) e a regra + ALNS (B) perdem
para a referência (C, urgência conhecida desde o início).

Para cada evento sorteia um minuto `tau` da jornada e uma casa que não estava entre as candidatas do dia e a
transforma em urgente (sem janela de horário), a partir do plano do dia calculado pela ALNS.

    .venv/bin/python -m engine.replan_exp data/instances/bomjesus --eventos 20 [--semente 0] [--microarea k]
                                          [--saida data/resultados/replan_base.csv]
"""

import argparse
import csv
import statistics
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from . import alns
from .instance import Params
from .io import ensure_matrix, instance_from_files
from .preprocess import to_visit
from .replan import estado_em, estender_por_no, inserir_regra, planejar_do_zero, reotimizar

RESULTADOS = Path(__file__).resolve().parents[1] / "data" / "resultados"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("instancia")
    ap.add_argument("--eventos", type=int, default=20)
    ap.add_argument("--semente", type=int, default=0)
    ap.add_argument("--microarea", type=int, default=None)
    ap.add_argument("--sementes-alns", default="0,1,2", help="sementes da ALNS na referência C e no plano do dia")
    ap.add_argument("--tempo-b", type=float, default=1.0, help="limite de tempo da ALNS da estratégia B, por semente (s)")
    ap.add_argument("--saida", type=Path, default=None)
    args = ap.parse_args(argv)

    seeds = [int(s) for s in args.sementes_alns.split(",")]
    p = Params(seeds=seeds, time_limit_s=5.0)
    p_b = replace(p, time_limit_s=args.tempo_b)
    files, pre = instance_from_files(args.instancia, params=p, microarea=args.microarea)
    inst = pre.instance
    nodes, t = ensure_matrix(files)
    plano = alns.solve_multi_seed(inst, p)
    print(f"plano do dia: {len(plano.route) - 2} visitas, retorno {plano.H:.0f} min, objetivo {plano.objective:.1f}")

    no_dia = {v.id for v in inst.visits[1:]} | {i for i, _ in pre.discarded}
    candidatas_evento = [h for h in files.households(args.microarea) if h.id not in no_dia]
    rng = np.random.default_rng(args.semente)
    linhas = []
    for ev in range(args.eventos):
        tau = float(round(rng.uniform(60, plano.H - 30)))
        h = candidatas_evento[int(rng.integers(len(candidatas_evento)))]
        nova = replace(to_visit(h, files.data_base), urgent=True, tw=(0.0, inst.Tmax))
        ext = estender_por_no(inst, nova, nodes, t)
        estado = estado_em(inst, plano.route, tau)
        restante = plano.route[len(estado.prefix):-1]
        A = inserir_regra(ext, p, estado, restante, inst.n)
        B = reotimizar(ext, p_b, estado, A, restante) if A.atendida else A
        C = planejar_do_zero(ext, p)
        linha = {"evento": ev, "tau": tau, "casa": h.id, "grave": int(nova.grave), "feitas": len(estado.prefix) - 1,
                 "restante_plano": len(restante)}
        for r in (A, B, C):
            k = r.estrategia[0]
            linha |= {f"{k}_atendida": int(r.atendida), f"{k}_retiradas": len(r.retiradas), f"{k}_objetivo": r.objetivo,
                      f"{k}_caminhada": r.caminhada, f"{k}_residual": r.residual, f"{k}_excesso": r.excesso,
                      f"{k}_H": r.H, f"{k}_cabe_em_Tmax": int(r.H <= inst.Tmax + 1e-9),
                      f"{k}_tempo_s": r.runtime_s}
        linhas.append(linha)
        print(f"  evento {ev:2d} · tau {tau:4.0f} · A {A.objetivo:8.1f} (−{len(A.retiradas)}) · "
              f"B {B.objetivo:8.1f} (−{len(B.retiradas)}) · C {C.objetivo:8.1f}", flush=True)
        sys.stdout.flush()

    ok = [l for l in linhas if l["A_atendida"] and l["C_objetivo"] < float("inf")]
    gap = lambda k: [100 * (l[f"{k}_objetivo"] - l["C_objetivo"]) / l["C_objetivo"] for l in ok]
    print(f"\n{len(ok)} de {len(linhas)} eventos com urgência atendida pela regra A")
    if ok:
        print(f"distância à referência C: A {statistics.fmean(gap('A')):.2f}% (pior {max(gap('A')):.2f}%) · "
              f"B {statistics.fmean(gap('B')):.2f}% (pior {max(gap('B')):.2f}%)")
        print(f"visitas retiradas: A {statistics.fmean(l['A_retiradas'] for l in ok):.1f} · "
              f"B {statistics.fmean(l['B_retiradas'] for l in ok):.1f}")
        print(f"tempo: A {1000 * statistics.fmean(l['A_tempo_s'] for l in ok):.1f} ms · "
              f"B {statistics.fmean(l['B_tempo_s'] for l in ok):.1f} s ({len(seeds)} sementes)")
    if args.saida:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        with args.saida.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(linhas[0]))
            w.writeheader()
            w.writerows(linhas)
        print(f"{len(linhas)} linhas gravadas em {args.saida}")


if __name__ == "__main__":
    main()
