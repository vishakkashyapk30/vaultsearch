# VaultSearch Evaluation Report

## Retrieval quality and latency

| Mode | NDCG@10 | MRR | p50 latency (ms) | p95 latency (ms) |
|---|---:|---:|---:|---:|
| bm25 | 0.783 | 0.708 | 0.8 | 1.3 |
| vector | 0.853 | 0.792 | 4.3 | 245.7 |
| hybrid | 0.844 | 0.750 | 5.4 | 5.9 |
| hybrid+rerank | 0.865 | 0.792 | 38.9 | 49.3 |

## Permission safety

- Adversarial retrieval attempts: 100
- Restricted-fact leaks: 0
- Unauthorized chunks returned: 0
- Permission leakage rate: 0.00%

All queries use retrieval-time ACL pre-filtering. The leakage metric also
searches returned chunk text for a distinctive restricted fact.
