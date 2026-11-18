# Resource profile

**Source:** `heavy.csv`  
**Platform:** Linux-6.18.55-1-MANJARO-x86_64-with-glibc2.44  
**Python:** 3.10.20  
**GPU:** not selected  
**Duration:** 13.97 s  
**CPU/RSS interval:** 0.01 s  
**USS/PSS interval:** 0.05 s  
**Samples:** 864

<details>
<summary>Profile description</summary>
## Тяжелый профайл

        первая фаза должна сильно нагрузить CPU,
        вторая должна вызвать скачок потребления RAM,
        третья это просто sleep(),
        четвертая грузит GC и создает много аллокаций,
        пятая нагружает cpu через криптографию

</details>

## Metrics

| Metric | Unit | Start | Min | Avg | Max | P95 | N |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **CPU** | | | | | | | |
| CPU utilization | % | 72.52 | 0.00 | 66.58 | 169.20 | 137.33 | 864 |
| CPU time | s | 0.01 | 0.01 | 4.21 | 9.32 | 8.65 | 864 |
| Processes |  | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 864 |
| **RAM** | | | | | | | |
| RSS | MiB | 21.72 | 21.72 | 138.47 | 409.68 | 409.68 | 864 |
| USS | MiB | 10.29 | 10.29 | 130.88 | 398.24 | 398.24 | 233 |
| PSS | MiB | 13.59 | 13.59 | 134.18 | 401.54 | 401.54 | 233 |

**CPU/RSS sampling:** 864 measurements; mean gap 0.02 s; max gap 0.02 s

**USS/PSS sampling:** 233 measurements; mean gap 0.06 s; max gap 0.07 s

## Phase: p1

Inclusive duration: 3.00 s; inclusive samples: 198

| Metric | Unit | Start | Min | Avg | Max | P95 | N |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **CPU** | | | | | | | |
| CPU utilization | % | 72.52 | 52.60 | 98.87 | 169.20 | 140.93 | 198 |
| CPU time | s | 0.01 | 0.01 | 1.49 | 2.96 | 2.81 | 198 |
| Processes |  | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 198 |
| **RAM** | | | | | | | |
| RSS | MiB | 21.72 | 21.72 | 21.72 | 21.72 | 21.72 | 198 |
| USS | MiB | 10.29 | 10.29 | 10.29 | 10.29 | 10.29 | 49 |
| PSS | MiB | 13.59 | 13.59 | 13.59 | 13.59 | 13.59 | 49 |

## Phase: p2

Inclusive duration: 2.93 s; inclusive samples: 178

| Metric | Unit | Start | Min | Avg | Max | P95 | N |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **CPU** | | | | | | | |
| CPU utilization | % | 129.32 | 0.00 | 14.51 | 141.35 | 119.69 | 178 |
| CPU time | s | 2.98 | 2.98 | 3.32 | 3.38 | 3.34 | 178 |
| Processes |  | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 178 |
| **RAM** | | | | | | | |
| RSS | MiB | 25.18 | 25.18 | 379.62 | 409.68 | 409.68 | 178 |
| USS | MiB | 14.14 | 14.14 | 367.82 | 398.24 | 398.24 | 49 |
| PSS | MiB | 17.45 | 17.45 | 371.13 | 401.54 | 401.54 | 49 |

## Phase: p3

Inclusive duration: 2.00 s; inclusive samples: 131

| Metric | Unit | Start | Min | Avg | Max | P95 | N |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **CPU** | | | | | | | |
| CPU utilization | % | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 131 |
| CPU time | s | 3.38 | 3.38 | 3.38 | 3.38 | 3.38 | 131 |
| Processes |  | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 131 |
| **RAM** | | | | | | | |
| RSS | MiB | 22.96 | 22.96 | 22.96 | 22.96 | 22.96 | 131 |
| USS | MiB | 11.52 | 11.52 | 11.52 | 11.52 | 11.52 | 34 |
| PSS | MiB | 14.82 | 14.82 | 14.82 | 14.82 | 14.82 | 34 |

## Phase: p4

Inclusive duration: 3.00 s; inclusive samples: 163

| Metric | Unit | Start | Min | Avg | Max | P95 | N |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **CPU** | | | | | | | |
| CPU utilization | % | 0.00 | 0.00 | 98.60 | 148.54 | 135.33 | 163 |
| CPU time | s | 3.38 | 3.38 | 4.84 | 6.33 | 6.18 | 163 |
| Processes |  | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 163 |
| **RAM** | | | | | | | |
| RSS | MiB | 30.55 | 30.55 | 127.24 | 128.74 | 128.74 | 163 |
| USS | MiB | 98.80 | 98.80 | 116.89 | 117.30 | 117.30 | 50 |
| PSS | MiB | 102.10 | 102.10 | 120.19 | 120.60 | 120.60 | 50 |

## Phase: p5

Inclusive duration: 3.00 s; inclusive samples: 194

| Metric | Unit | Start | Min | Avg | Max | P95 | N |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **CPU** | | | | | | | |
| CPU utilization | % | 103.87 | 56.40 | 99.46 | 154.83 | 143.62 | 194 |
| CPU time | s | 6.35 | 6.35 | 7.83 | 9.32 | 9.17 | 194 |
| Processes |  | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 194 |
| **RAM** | | | | | | | |
| RSS | MiB | 123.82 | 123.82 | 123.82 | 123.82 | 123.82 | 194 |
| USS | MiB | 112.38 | 112.38 | 112.38 | 112.38 | 112.38 | 51 |
| PSS | MiB | 115.68 | 115.68 | 115.68 | 115.68 | 115.68 | 51 |

## Notes

- CPU utilization is relative to one logical core (100% = one core).
- Missing USS/PSS values between their sampling intervals are not forward-filled.
- Missing values appear as `—`; sampling can miss brief peaks.
