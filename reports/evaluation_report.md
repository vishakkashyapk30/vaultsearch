# VaultSearch Evaluation Report

## Retrieval quality and latency

| Mode | NDCG@10 | MRR | p50 latency (ms) | p95 latency (ms) |
|---|---:|---:|---:|---:|
| bm25 | 0.783 | 0.708 | 1.4 | 4.4 |
| vector | 0.853 | 0.792 | 5.1 | 403.2 |
| hybrid | 0.843 | 0.750 | 7.3 | 19.5 |
| hybrid+rerank | 0.865 | 0.792 | 50.4 | 81.4 |

## Permission safety

- Adversarial retrieval attempts: 100
- Restricted-fact leaks: 0
- Unauthorized chunks returned: 0
- Permission leakage rate: 0.00%

All queries use retrieval-time ACL pre-filtering. The leakage metric also
searches returned chunk text for a distinctive restricted fact.
