# Evaluation: v3-in-sample

Model `sentinel-logistic-risk-v3`, 227 labelled incidents, 28 golden questions.

## Severity

- Accuracy: **98%** (hazards 100% of 169, news 91% of 58)
- Macro F1: **0.98**

| Severity | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| low | 0.96 | 1.00 | 0.98 | 111 |
| medium | 1.00 | 0.86 | 0.93 | 36 |
| high | 1.00 | 1.00 | 1.00 | 40 |
| critical | 1.00 | 1.00 | 1.00 | 40 |

Confusion (rows: label, columns: predicted):

| | low | medium | high | critical |
|---|---|---|---|---|
| low | 111 | 0 | 0 | 0 |
| medium | 5 | 31 | 0 | 0 |
| high | 0 | 0 | 40 | 0 |
| critical | 0 | 0 | 0 | 40 |

## Retrieval (top 4)

| Method | Hit rate | Recall@4 | Place | Event | Theme |
|---|---|---|---|---|---|
| semantic | 96% | 0.83 | 0.90 | 0.74 | 0.79 |
| keyword | 93% | 0.73 | 0.93 | 0.50 | 0.57 |

Per question (recall@4, semantic / keyword):

- earthquakes in Japan: 0.75 / 0.75
- earthquake in the Philippines: 0.75 / 1.00
- Indonesia earthquakes: 0.75 / 1.00
- Tonga earthquake: 1.00 / 1.00
- Solomon Islands quake: 1.00 / 1.00
- New Caledonia earthquakes: 1.00 / 1.00
- earthquake in Afghanistan: 1.00 / 1.00
- Kamchatka earthquake: 0.67 / 0.33
- Taiwan earthquake: 1.00 / 1.00
- Costa Rica earthquake: 1.00 / 1.00
- Alaska earthquake: 1.00 / 1.00
- Mid-Atlantic Ridge earthquakes: 0.75 / 1.00
- Papua New Guinea earthquake: 1.00 / 1.00
- Yemen earthquake: 1.00 / 1.00
- flood alerts: 1.00 / 1.00
- flooding and displaced people: 0.00 / 0.00
- tropical cyclones: 1.00 / 1.00
- Myanmar airstrike: 0.50 / 0.25
- Israeli strikes in Gaza: 0.75 / 0.75
- Gaza humanitarian situation: 1.00 / 0.00
- Hamas commander killed: 1.00 / 0.50
- Sudan ceasefire: 0.67 / 0.33
- Iran US ceasefire talks: 1.00 / 0.50
- attacks on shipping in the Strait of Hormuz: 1.00 / 0.67
- oil prices and the Iran war: 0.75 / 0.75
- Ukraine Russia peace talks: 0.67 / 0.33
- shelling in Kyiv: 0.33 / 0.33
- Myanmar refugees deported from Malaysia: 1.00 / 1.00
