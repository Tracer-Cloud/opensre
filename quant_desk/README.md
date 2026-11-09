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

Pré-requisitos: Python 3.11+ (testado em 3.13) e acesso de rede a `data-api.binance.vision`.

```bash
cd quant_desk
python -m venv .venv            # ou: uv venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_desk.py              # baixa dados novos e roda tudo (~20 s)
.venv/bin/python run_desk.py --no-fetch   # reaproveita as barras já gravadas no ArcticDB
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

## Regras de execução (para não se enganar)

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
- Faça paper trade por semanas antes de pensar em operar ao vivo.
- **Licença do ArcticDB**: Business Source License 1.1. Uso em produção é permitido, exceto
  oferecer o ArcticDB como serviço de banco de dados. Cada versão vira Apache 2.0 dois anos
  após o lançamento. Confira os termos da versão que você usa antes de qualquer uso comercial.
- gs-quant não tem função de perfil de volume; aquele cálculo é um histograma próprio
  (`desk/indicators.py`).
- Os dados são BTC/**USDT** da Binance, usado como proxy de BTC/USD.
