# Mesa quant no laptop — BTC/USDT, csp + gs-quant + ArcticDB

Pipeline completo e local (sem caixa preta: código e dados ficam com você):

1. baixa 30 dias de barras de 1 minuto de BTC/USDT da API pública da Binance (sem chave);
2. reamostra para 15m e grava as duas séries numa biblioteca local **ArcticDB** (LMDB);
3. lê de volta do ArcticDB e calcula EMA, Bandas de Bollinger e RSI com
   `gs_quant.timeseries`, mais um perfil de volume (POC e value area de 70%);
4. reproduz as barras como stream no **csp** e roda um cruzamento de EMA long/short com
   taxa de 2 bps;
5. varre **212 combos** (rápida 4–24 passo 2 × lenta 12–96 passo 4, com lenta ≥ 1,5 × rápida)
   num único grafo csp, classifica por Sharpe, divide os 30 dias em 4 janelas e rotula cada
   combo como **platô** ou **pico**;
6. desenha cada LONG e SHORT na barra exata e grava o log do agente com o mesmo timestamp;
7. roda um **walk-forward** (escolhe na janela k, opera na k+1) para comparar com a "tela".

## Rodar

Pré-requisitos: Python 3.10+ (testado em 3.13) e acesso de rede a `data-api.binance.vision`.

```bash
cd quant_desk
python -m venv .venv            # ou: uv venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_desk.py              # baixa dados novos e roda tudo (~20 s)
.venv/bin/python run_desk.py --no-fetch   # reaproveita as barras já gravadas no ArcticDB
.venv/bin/python run_history.py           # tudo: estudo, robustez, moedas, universo, corretoras (~30 min)
.venv/bin/python run_history.py --no-fetch
.venv/bin/python run_paper.py             # paper trading: um passo (ver abaixo)
.venv/bin/python run_paper.py --loop      # paper trading 24/7
.venv/bin/python -m pytest                # testes (dados sintéticos, sem rede)
```

Saídas em `output/`:

| arquivo | conteúdo |
|---|---|
| `REPORT.md` | ranking, platô vs pico, walk-forward, indicadores na última barra |
| `trades.png` | preço, EMAs, Bollinger, todos os trades, RSI, equity vs buy & hold, perfil de volume |
| `trades_zoom.png` | últimos 2 dias em candles, cada trade rotulado com a hora da barra |
| `sweep_heatmap.png` | Sharpe nos 212 combos, picos e platôs marcados |
| `windows.png` | equity dos 5 melhores dentro de cada janela |
| `walk_forward.png` | walk-forward fora da amostra vs a tela e buy & hold |
| `agent.log` | uma linha por trade, com o timestamp da barra que disparou |
| `sweep.csv` | os 212 combos com todas as métricas |

O ArcticDB fica em `data/arctic` (símbolos `BTCUSDT_1m`, `BTCUSDT_15m`,
`BTCUSDT_15m_ema_sweep`; cada execução grava uma nova versão).

## Estudo de vários anos (`run_history.py`)

Barras de 15m de BTC/USDT desde ago/2017 (bear de 2018, bull de 2021, crash de 2022, bull de
2024–25 e a lateralização recente), reamostradas para 1h, 4h, 8h e 1d e gravadas no ArcticDB.
Roda em processos paralelos. Cada variante é varrida na grade inteira em cada tamanho de barra:

| variante | o que faz |
|---|---|
| `always_in` | original: cruzamento de EMA, sempre long ou short |
| `gap_cooldown` | só entra com distância entre EMAs ≥ 0,25 ATR e espera 4 barras entre trades; fica flat no resto |
| `flat_er` | segue a direção das EMAs só com eficiência (ER 20) ≥ 0,30; flat no resto |
| `flat_adx` | idem com ADX 14 ≥ 25 |
| `flat_adx_gap_cd` | ADX + distância mínima + cooldown |
| `long_flat` | comprado quando EMA rápida > lenta, flat no resto (nunca vendido) |
| `long_flat_gap_cd` | long/flat + distância mínima + cooldown |
| `long_flat_adx` | long/flat só com ADX 14 ≥ 25 |
| `mr_always` | reversão à média: compra abaixo da banda de Bollinger, vende acima, sai na média |
| `mr_chop` | igual, mas só quando o mercado está lateral (ER < 0,30) |

Os filtros usam valores de livro e **não foram otimizados**. A história é dividida em 8 janelas;
platô exige vizinhos com ≥ 50% do Sharpe e lucro em ≥ 6 de 8 janelas. Tudo é recalculado a
15 bps com execução na abertura seguinte (ver abaixo) e passa por walk-forward de 7 passos.

### Construção robusta (sem escolher parâmetro)

Em vez de escolher um combo, cada variante opera **a grade inteira de uma vez**: a exposição é a
média das posições dos 212 combos, numa única conta (taxa sobre a mudança líquida). Cada uma é
avaliada em tamanho cheio e com **alvo de volatilidade** (exposição × min(1, 40% / vol de 30
dias), ajustada 1×/dia, sem alavancagem). O buy & hold recebe o mesmo alvo de vol, então a
comparação é justa.

Regra definida **antes** de ver os resultados: em W2–W8 a 15 bps, Sharpe maior **e** drawdown
máximo menor que o buy & hold equivalente. **Robusto** = passa em pelo menos dois tamanhos de
barra vizinhos (15m → 1h → 4h → 8h → 1d).

Saídas em `output/history/`: `HISTORY_REPORT.md`, **`ROBUSTNESS_REPORT.md`**, `summary.csv`,
`sweeps/` (uma tabela por variante e barra), `candidates.csv`, `execution_test.csv`,
`meta_walk_forward.csv`, `deflated_sharpe.csv`, `sensitivity.csv`, `assets.csv`,
`portfolio.csv`, `long_short_holdout.csv`, `universe.csv`, `universe_membership.csv`,
`universe_casualties.csv`, `venues.csv` e os gráficos `wf_sharpe.png`, `heatmaps_trend.png`,
`heatmaps_mean_reversion.png`, `wf_equity.png`, `candidates.png`, `portfolio.png`, `holdouts.png`.

Rede necessária: `data-api.binance.vision`, `www.bitstamp.net`, `api.exchange.coinbase.com`.

### Custos e execução (premissas principais)

Toda decisão é tomada no fechamento da barra e **executada na abertura da barra seguinte**,
pagando **15 bps** por unidade de mudança de posição (10 de taxa taker + 5 de slippage).
2 bps (VIP/maker) aparece só como referência otimista. O BTC agora começa em ago/2017, então a
amostra inclui o bear de 2018 e o crash de 2022.

### Checagens de robustez (`ROBUSTNESS_REPORT.md`)

| ressalva | o que foi feito |
|---|---|
| escolher a melhor linha é seleção dentro da amostra | walk-forward "meta" escolhe variante, tamanho e barra só com dados passados + Deflated Sharpe Ratio |
| filtros com valores de livro | grade de sensibilidade (vol alvo, gap, cooldown, ADX) com o pior caso, sem escolher nada |
| "não operar" vencendo o walk-forward | só combos com ≥ 3 trades na janela de treino podem ser escolhidos |
| 2 bps é otimista | 15 bps + execução na abertura seguinte em todos os números principais |
| um ativo, amostra de alta | BTC desde 2017, os mesmos livros (sem reajuste) em 9 moedas e num portfólio igual-peso |
| execução idealizada | teste com fechamento, abertura seguinte e uma barra de atraso |
| long-only escolhido depois de ver os dados | cada livro long/short comparado ao seu gêmeo long/flat só em dados que a decisão nunca viu: BTC Bitstamp 2013–2017, BTC Binance 2017–2021, outras moedas e o universo sem viés |
| viés de sobrevivência | universo point-in-time: todo mês, top 10 por volume entre **todos** os pares USDT que a Binance já listou (incl. deslistados); tickers reaproveitados (LUNA antiga → nova) são separados |
| só Binance | os mesmos livros de BTC na Bitstamp (desde 2013) e na Coinbase (desde 2015) |

### Paper trading

`run_paper.py` opera no papel o livro registrado antes dos testes (`PAPER_BOOKS` em
`desk/config.py`: BTC 4h `long_flat_gap_cd` com alvo de vol). Cada decisão é gravada num
ledger que só aceita inclusões (ArcticDB, biblioteca `paper`) no momento em que é tomada e
nunca é recalculada. Se o bot ficou desligado, as decisões atrasadas são marcadas `late` e só
valem a partir de quando foram tomadas. Status em `output/paper/STATUS.md`.

```bash
nohup .venv/bin/python run_paper.py --loop >> output/paper/paper.log 2>&1 &   # 24/7
# ou cron a cada 4h:  2 */4 * * *  cd /caminho/quant_desk && .venv/bin/python run_paper.py
```

## Resultado da última execução completa

Tudo a 15 bps com execução na abertura seguinte, fora da amostra (W2–W8). Detalhes em
`output/history/ROBUSTNESS_REPORT.md`.

- **Escolha sem hindsight** (meta walk-forward entre 80 livros): Sharpe 0,96 vs 0,81 do buy &
  hold, drawdown −44% vs −77% — mas retorno total **menor** (+632% vs +1080%). O ganho é risco
  menor, não mais dinheiro.
- **Pode ser sorte?** Deflated Sharpe: 1 de 80 livros é significativo sozinho e **nenhum** tem
  Sharpe significativamente acima do buy & hold.
- **Long-only escolhido depois de ver os dados**: em dados que a decisão nunca viu, long/flat
  teve Sharpe ≥ long/short em 98% de 180 comparações e o lado vendido perdeu dinheiro em 87%.
  Exceção: Bitstamp 2014 (bear), onde vender rendeu mais, com Sharpe menor.
- **Viés de sobrevivência**: universo point-in-time com 199 pares deslistados de volta. Todos os
  livros long/flat passam (8h com alvo de vol: Sharpe 1,10 vs 0,52, drawdown −30% vs −61%); o
  buy & hold igual-peso do mesmo top 10 caiu até −94%. O viés antigo era pequeno (~0,05 de Sharpe).
- **Outras corretoras**: BTC 2013–2017 na Bitstamp, que nenhuma decisão viu: 18/18 livros passam.

Continua aberto: a escolha long-only pode ser testada mas não "desvista"; moedas que nunca
foram listadas na Binance não entram; tudo é cripto e histórico. O único teste limpo é o
paper trading rodando por semanas **na sua máquina** (`run_paper.py --loop`).

## Regras de execução da tela de 30 dias (`run_desk.py`)

- As barras são carimbadas no **fechamento**. O cruzamento na barra *t* executa no fechamento
  de *t* e só ganha o movimento de *t* → *t+1*. Um teste injeta lookahead e verifica que ele é pego.
- A taxa é 2 bps por unidade de mudança de posição: entrada 2 bps, virada long↔short 4 bps.
- A estratégia fica sempre posicionada depois do primeiro cruzamento (stop-and-reverse).
- Sharpe anualizado com √(365 × 96) sobre retornos de 15m, já descontadas as taxas.
- **Platô**: os vizinhos na grade (±1 passo) mantêm ≥ 50% do Sharpe **e** o combo é positivo
  em ≥ 3 de 4 janelas. **Pico**: qualquer outro combo com Sharpe > 0.

## Leia antes de confiar

- O melhor combo é escolhido nos mesmos dados em que é mostrado. É uma tela, não prova.
  Olhe o walk-forward e o `REPORT.md`, que mostra quanto do resultado veio de um único dia.
- 30 dias geram só 3 passos de walk-forward. Rode com mais histórico (`LOOKBACK_DAYS` em
  `desk/config.py`) antes de concluir qualquer coisa.
- Faça paper trade por semanas antes de pensar em operar ao vivo (`run_paper.py`).
- **Licença do ArcticDB**: Business Source License 1.1. Uso em produção é permitido, exceto
  oferecer o ArcticDB como serviço de banco de dados. Cada versão vira Apache 2.0 dois anos
  após o lançamento. Confira os termos da versão que você usa antes de qualquer uso comercial.
- gs-quant não tem função de perfil de volume; aquele cálculo é um histograma próprio
  (`desk/indicators.py`).
- O BTC/**USDT** da Binance é o dado principal. BTC/USD da Bitstamp e da Coinbase confirmam o
  resultado no BTC (correlação diária 0,997); as altcoins continuam só na Binance.
