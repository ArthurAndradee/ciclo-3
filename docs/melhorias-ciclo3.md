# Ciclo 3: reprodução, ALNS com muitas urgências e urgência no meio do dia

Registro do trabalho do Ciclo 3 sobre o MVP do Ciclo 2. Datas: 05/11/2026 (reprodução do cenário base) e 12/11/2026 (demais cenários e melhorias). Os números abaixo vêm de execuções em um notebook Windows (i5-8265U, 8 GB de RAM), com as instâncias geradas do zero.

## 1. Reprodução dos resultados do Ciclo 2

As 5 instâncias foram geradas de novo com a mesma UBS, semente 0 e `--data-base 2026-10-08`, e validadas com `engine.validate` (10 sementes na ALNS, 5 sorteios de 10 candidatas para o MILP).

| Cenário | Custo guloso (pub. × nosso) | Custo ALNS (pub. × nosso) | Gap da ALNS (pub. × nosso) | Pior gap da ALNS (pub. × nosso) |
|---|---|---|---|---|
| base | 3220,3 × 3220,3 | 3185,9 × 3185,9 | 0,33% × 0,33% | 1,67% × 1,67% |
| aglomerado | 2857,3 × 2857,3 | 2795,2 × 2795,2 | 0,40% × 0,44% | 2,22% × 2,22% |
| alta urgência | 5183,6 × 5183,6 | 5021,9 × 5021,9 | 2,96% × 2,96% | 14,78% × 14,78% |
| alto atraso | 6256,8 × 6256,8 | 6181,1 × 6181,1 | 0,00% × 0,00% | 0,00% × 0,00% |
| equipe de 4 ACS | 12085,9 × 12118,9 | 11839,4 × 11844,0 | 0,02% × 0,02% | 0,43% × 0,43% |

- Os 4 cenários de um ACS reproduzem os custos exatamente. A equipe de 4 difere 0,3% nos custos (a divisão em microáreas provavelmente saiu um pouco diferente), e o gap da ALNS bate.
- O motor passa nos 128 testes originais.

**Divergência não explicada.** O gap do guloso nas subinstâncias de 10 candidatas difere do publicado em todos os cenários, nos dois sentidos:

| Cenário | Publicado | Nosso |
|---|---|---|
| base | 5,39% | 6,90% |
| aglomerado | 3,24% | 0,79% |
| alta urgência | 6,57% | 7,05% |
| alto atraso | 11,33% | 7,84% |
| equipe de 4 ACS | 22,26% | 10,19% |

Foi descartado: o guloso é determinístico; os pesos β e γ não alteram o resultado nessas subinstâncias; o custo do guloso na instância completa bate; o MILP e a ALNS batem nas mesmas subinstâncias (portanto os sorteios são os mesmos). Não foi possível determinar a causa: o código está todo em um único commit e o CSV original (`validacao.csv`) não está no repositório. A hipótese mais plausível é que a tabela publicada veio de uma cópia local ainda não commitada do código. A divergência afeta só essa métrica secundária.

## 2. ALNS em dias com muitas urgências

**Diagnóstico.** O gap de 2,96% da alta urgência vem de um único dos 5 sorteios, com 6 urgências. Nele, as 10 sementes da ALNS terminam com o mesmo gap do guloso (14,78%): a ALNS nunca melhora o ponto de partida. O plano ótimo visita as mesmas casas com outra ordem das urgências (a urgência 1 primeiro, e não por último) e caminha 40,4 min, contra 46,4 min da ALNS. A ordem das urgências vem do guloso (vizinho mais próximo) e a ALNS só a mexia por 2-opt e or-opt, que já existiam em `alns.py` e aceitam um movimento por vez. Mudar a ordem de uma urgência piora o plano até o restante da rota ser reordenado junto, e a busca local não faz os dois passos.

**Mudança.** Novo operador `destroy_urgent_order` ([backend/engine/alns.py](../backend/engine/alns.py)): troca duas urgências, move uma ou inverte um trecho do bloco inicial e, na mesma iteração, destrói parte do restante. O Simulated Annealing decide se aceita. Sem pelo menos 2 urgências, ou se a nova ordem violar janela ou jornada, o operador se comporta como `destroy_random`.

**Resultado** (mesmos 5 sorteios, ALNS × MILP):

| Cenário | Gap médio (antes → depois) | Pior caso (antes → depois) | ALNS acha o ótimo |
|---|---|---|---|
| base | 0,33% → 0,00% | 1,67% → 0,00% | 40 → 50 de 50 |
| aglomerado | 0,44% → 0,00% | 2,22% → 0,00% | 40 → 50 de 50 |
| alta urgência | 2,96% → 0,00% | 14,78% → 0,00% | 40 → 50 de 50 |
| alto atraso | 0,00% → 0,00% | 0,00% → 0,00% | 50 → 50 de 50 |
| equipe de 4 ACS | 0,02% → 0,00% | 0,43% → 0,00% | 192 → 200 de 200 |

**Fora da amostra.** Como o operador foi desenhado olhando esses sorteios, foi testado em 20 sorteios novos por cenário (semente de sorteio 100 a 119), 5 sementes da ALNS, com e sem o operador:

| Cenário | Sem o operador: gap médio / pior / ótimos | Com o operador: gap médio / pior / ótimos |
|---|---|---|
| base | 0,00% / 0,00% / 100 de 100 | 0,00% / 0,00% / 100 de 100 |
| aglomerado | 0,00% / 0,00% / 100 de 100 | 0,00% / 0,00% / 100 de 100 |
| alta urgência | 0,35% / 6,20% / 91 de 100 | 0,00% / 0,00% / 100 de 100 |
| alto atraso | 0,00% / 0,00% / 100 de 100 | 0,00% / 0,00% / 100 de 100 |

O efeito fora da amostra é menor que nos 5 sorteios originais, que eram especialmente desfavoráveis. Nas instâncias completas o ganho é pequeno (a melhor solução da equipe de 4 melhorou 0,02%); o tempo por semente não aumentou.

## 3. Urgência que surge durante o dia

Módulo [backend/engine/replan.py](../backend/engine/replan.py). No minuto `tau`, uma casa vira urgente. O que o ACS já visitou não muda; o restante do dia é replanejado. Estratégias comparadas:

| Estratégia | O que faz |
|---|---|
| A. Regra da proposta | Insere a urgência logo depois da visita em andamento. Se passar de Tmax (ou violar janela ou teto de graves), retira as visitas não urgentes de menor penalidade, uma a uma |
| A2. Sem hora extra | Como A, mas retira visitas até voltar dentro da jornada nominal T. Se não couber, cai em A |
| B. A + ALNS no restante | Parte da rota da regra A e roda a ALNS só no que falta do dia, com o feito travado |
| C. Referência | Replaneja o dia inteiro como se a urgência fosse conhecida desde o início. Não é aplicável na prática; serve de limite |

Todas são comparadas pelo mesmo objetivo do dia (caminhada total + β · penalidade residual + γ · excesso de jornada). A posição do ACS é aproximada: se ele está caminhando entre duas paradas, é tratado como estando na parada anterior, com o tempo gasto contado.

**Experimento** (`engine.replan_exp`): para cada evento, um minuto sorteado entre 60 e (retorno − 30) e uma casa fora das candidatas do dia, que vira urgente sem janela de horário. 20 eventos por cenário de um ACS; 8 por microárea na equipe de 4. Distância média à referência C, só nos eventos em que a regra A coube:

| Cenário | A | A2 | B | Urgências atendidas |
|---|---|---|---|---|
| base | 11,1% | 4,2% | 1,6% | 20 de 20 |
| alto atraso | 4,8% | 3,9% | 1,3% | 20 de 20 |
| alta urgência | 2,8% | 2,8% | 1,2% | 12 de 20 |
| equipe de 4 ACS (média das 4 microáreas) | 10,1% | 3,2% | 1,2% | 31 de 32 |

- A regra da proposta quase não retira visitas, porque só retira quando passa de Tmax. Em vez disso, o ACS faz em média de 21 a 23 min de hora extra (base e alto atraso), o que custa caro com γ = 20.
- B (cerca de 2,5 s com 3 sementes de 1 s) foi melhor que A em todos os eventos de base e alto atraso e nunca pior que A em nenhum evento.
- Nos 9 eventos sem solução (8 na alta urgência, 1 na equipe), a urgência não cabe nem sem nenhuma visita não urgente. `replanejar()` devolve `urgencia_excedente` com os minutos que faltam, para a gestão decidir.
- **Ressalva da referência.** Na alta urgência, a referência C passa de Tmax em 3 dos 8 eventos sem solução. As distâncias da tabela só usam eventos em que a referência é viável.

## 4. Limitações

- Dados sintéticos: os números de demanda e de urgência dizem mais sobre o gerador do que sobre uma equipe real.
- O experimento da urgência usa eventos sorteados; não há dados de urgências reais nem plano feito por pessoas.
- A urgência sorteada não tem janela de horário e a posição do ACS é aproximada (seção 3).
- O replanejamento ainda não está ligado à API Django nem à tela Angular; o sistema completo (banco via Docker) não foi testado neste notebook.
- Os CSVs de resultados ficam em `backend/data/resultados/`, fora do git.

## 5. Como reproduzir

A partir de `backend/`, com o ambiente e as instâncias gerados como no [README](../README.md). No Windows, use `PYTHONUTF8=1` e deixe a variável `SSLKEYLOGFILE` fora do ambiente.

```sh
# um CSV por cenário (o validate sobrescreve o arquivo de saída a cada execução)
.venv/bin/python -m engine.validate --cenarios base=data/instances/bomjesus --saida data/resultados/v_base.csv
# demais cenários: val_aglomerado (--aglomerado), val_urgencia (--urgencia 0.15), val_atraso (--atraso 3),
# bomjesus_eq4 (--acs 4); todos com --ubs=-30.0431944,-51.1563369 --data-base 2026-10-08

# urgência no meio do dia
.venv/bin/python -m engine.replan_exp data/instances/bomjesus --eventos 20 --saida data/resultados/replan_base.csv
.venv/bin/python -m engine.replan_exp data/instances/bomjesus_eq4 --microarea 1 --eventos 8

.venv/bin/python -m pytest tests/engine   # 169 testes, sem banco nem rede
```
