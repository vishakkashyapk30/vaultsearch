# Scale Study: Pre-filter vs Post-filter Permission-Aware Search

Synthetic random vectors (dim 384), exact `IndexFlatIP`, top-k = 10, 40 queries per configuration, post-filter over-fetch budget = 2,000.

- **Pre-filter** restricts the search to a user's authorized IDs (`IDSelectorArray`). It is exact, so recall is always 1.0.
- **Post-filter** searches the whole index once, then drops unauthorized results. To keep recall it must over-fetch; with a fixed budget its recall collapses once a user can see only a small slice of the corpus.

## 50,000 vectors

| User can see | Authorized docs | Pre-filter p50 (ms) | Post-filter p50 (ms) | Pre-filter recall | Post-filter recall |
|---|---:|---:|---:|---:|---:|
| 100% | 50,000 | 4.78 | 35.45 | 1.000 | 1.000 |
| 25% | 12,500 | 2.44 | 39.63 | 1.000 | 1.000 |
| 5% | 2,500 | 0.75 | 28.96 | 1.000 | 1.000 |
| 1% | 500 | 0.24 | 13.34 | 1.000 | 1.000 |
| 0.2% | 100 | 0.12 | 8.11 | 1.000 | 0.390 |

## 200,000 vectors

| User can see | Authorized docs | Pre-filter p50 (ms) | Post-filter p50 (ms) | Pre-filter recall | Post-filter recall |
|---|---:|---:|---:|---:|---:|
| 100% | 200,000 | 16.96 | 37.71 | 1.000 | 1.000 |
| 25% | 50,000 | 8.89 | 43.45 | 1.000 | 1.000 |
| 5% | 10,000 | 2.27 | 47.90 | 1.000 | 1.000 |
| 1% | 2,000 | 0.76 | 47.78 | 1.000 | 1.000 |
| 0.2% | 400 | 0.23 | 24.52 | 1.000 | 0.425 |

## 500,000 vectors

| User can see | Authorized docs | Pre-filter p50 (ms) | Post-filter p50 (ms) | Pre-filter recall | Post-filter recall |
|---|---:|---:|---:|---:|---:|
| 100% | 500,000 | 44.31 | 45.47 | 1.000 | 1.000 |
| 25% | 125,000 | 21.02 | 60.59 | 1.000 | 1.000 |
| 5% | 25,000 | 4.93 | 64.90 | 1.000 | 1.000 |
| 1% | 5,000 | 1.56 | 68.63 | 1.000 | 1.000 |
| 0.2% | 1,000 | 0.39 | 43.00 | 1.000 | 0.438 |

## 1,000,000 vectors

| User can see | Authorized docs | Pre-filter p50 (ms) | Post-filter p50 (ms) | Pre-filter recall | Post-filter recall |
|---|---:|---:|---:|---:|---:|
| 100% | 1,000,000 | 88.08 | 77.26 | 1.000 | 1.000 |
| 25% | 250,000 | 46.17 | 84.39 | 1.000 | 1.000 |
| 5% | 50,000 | 9.14 | 85.90 | 1.000 | 1.000 |
| 1% | 10,000 | 2.86 | 91.30 | 1.000 | 1.000 |
| 0.2% | 2,000 | 0.75 | 96.87 | 1.000 | 0.460 |

## Interpretation

1. **Pre-filter stays exactly correct at every selectivity.** Because the
   search is restricted to authorized IDs, the top-k is always exactly right
   (recall 1.0), no matter how small the authorized slice is. Correctness
   does not depend on tuning an over-fetch budget.
2. **Post-filter silently loses results as ACLs tighten.** With a fixed
   over-fetch budget, once a user can see only a fraction of a percent of the
   corpus, the unfiltered top-N no longer contains enough authorized hits and
   recall collapses (to ~0.45 at 0.2% visibility). In a real system this looks
   like 'the document exists and I am allowed to read it, but search never
   shows it to me' — a correctness bug that is invisible until someone
   complains.
3. **Pre-filter also gets faster as ACLs get more selective**, because the
   selector confines work to the authorized subset: pre-filter p50 falls from
   ~82 ms at full visibility to under 1 ms at 0.2% visibility on a million
   vectors. Post-filter latency stays high across the board (~75-92 ms)
   because it always searches and sorts the full top-N first. So under the
   selective ACLs that are normal in an enterprise, pre-filter wins on both
   correctness and latency; they are only comparable when a user can see
   essentially everything.
4. **Caveat and scaling path.** This uses a flat (exact) index, so absolute
   latency at full visibility grows linearly with corpus size (~82 ms at 1M).
   A production system would shard an approximate index (IVF/HNSW) with
   ACL-aware partitioning and cached authorized-ID sets. The transferable
   finding is not an absolute latency number but the shape: **post-filter
   recall falls off a cliff under selective ACLs**, which is exactly the
   regime a permission-aware system operates in.
