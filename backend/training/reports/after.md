### google/gemma-2b-it (zero-shot) [local raw]

n=117 · valid 100.0% · action match **0.0%** · exact match **0.0%** · slot match 0.0% · caller-rights recall 0.0% (GATE FAILED) · latency p50 1679.1 ms / p95 1891.1 ms

| category | n | action | exact | slots |
|---|---:|---:|---:|---:|
| affirm | 8 | 0.0% | 0.0% | — |
| ask_balance | 2 | 0.0% | 0.0% | — |
| ask_purpose | 2 | 0.0% | 0.0% | — |
| cannot_pay | 4 | 0.0% | 0.0% | 0.0% |
| closing | 2 | 0.0% | 0.0% | — |
| deny | 6 | 0.0% | 0.0% | — |
| discount | 2 | 0.0% | 0.0% | — |
| dispute | 2 | 0.0% | 0.0% | — |
| dob_at_greeting | 5 | 0.0% | 0.0% | 0.0% |
| dob_full | 8 | 0.0% | 0.0% | 0.0% |
| dob_partial | 8 | 0.0% | 0.0% | 0.0% |
| dob_phase_number | 5 | 0.0% | 0.0% | — |
| dob_split_year | 8 | 0.0% | 0.0% | 0.0% |
| injection | 2 | 0.0% | 0.0% | 0.0% |
| multi_intent | 3 | 0.0% | 0.0% | 0.0% |
| noisy_stt | 2 | 0.0% | 0.0% | — |
| pay_amount | 8 | 0.0% | 0.0% | 0.0% |
| pay_amount_date | 8 | 0.0% | 0.0% | 0.0% |
| pay_correction | 5 | 0.0% | 0.0% | 0.0% |
| pay_date_only | 5 | 0.0% | 0.0% | 0.0% |
| request_human | 8 | 0.0% | 0.0% | — |
| stop_contact | 8 | 0.0% | 0.0% | — |
| unclear | 3 | 0.0% | 0.0% | — |
| wrong_person | 3 | 0.0% | 0.0% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 0.0% | 0.0% | 0.0% |
| ja | 44 | 0.0% | 0.0% | 0.0% |

### google/gemma-2b-it + training/adapter [local raw]

n=117 · valid 100.0% · action match **84.6%** · exact match **82.9%** · slot match 91.5% · caller-rights recall 94.1% (GATE FAILED) · latency p50 1217.7 ms / p95 1442.8 ms

| category | n | action | exact | slots |
|---|---:|---:|---:|---:|
| affirm | 8 | 87.5% | 87.5% | — |
| ask_balance | 2 | 100.0% | 100.0% | — |
| ask_purpose | 2 | 50.0% | 50.0% | — |
| cannot_pay | 4 | 75.0% | 75.0% | 100.0% |
| closing | 2 | 50.0% | 50.0% | — |
| deny | 6 | 66.7% | 66.7% | — |
| discount | 2 | 0.0% | 0.0% | — |
| dispute | 2 | 50.0% | 50.0% | — |
| dob_at_greeting | 5 | 100.0% | 100.0% | 100.0% |
| dob_full | 8 | 100.0% | 87.5% | 87.5% |
| dob_partial | 8 | 87.5% | 75.0% | 75.0% |
| dob_phase_number | 5 | 60.0% | 60.0% | — |
| dob_split_year | 8 | 87.5% | 87.5% | 87.5% |
| injection | 2 | 0.0% | 0.0% | 0.0% |
| multi_intent | 3 | 0.0% | 0.0% | 100.0% |
| noisy_stt | 2 | 100.0% | 100.0% | — |
| pay_amount | 8 | 100.0% | 100.0% | 100.0% |
| pay_amount_date | 8 | 100.0% | 100.0% | 100.0% |
| pay_correction | 5 | 100.0% | 100.0% | 100.0% |
| pay_date_only | 5 | 100.0% | 100.0% | 100.0% |
| request_human | 8 | 100.0% | 100.0% | — |
| stop_contact | 8 | 100.0% | 100.0% | — |
| unclear | 3 | 100.0% | 100.0% | — |
| wrong_person | 3 | 100.0% | 100.0% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 84.9% | 84.9% | 94.9% |
| ja | 44 | 84.1% | 79.5% | 85.0% |

### google/gemma-2b-it (zero-shot) [local raw]

n=117 · valid 100.0% · action match **0.0%** · exact match **0.0%** · slot match 0.0% · caller-rights recall 0.0% (GATE FAILED) · latency p50 1418.6 ms / p95 1578.3 ms

| category | n | action | exact | slots |
|---|---:|---:|---:|---:|
| affirm | 8 | 0.0% | 0.0% | — |
| ask_balance | 2 | 0.0% | 0.0% | — |
| ask_purpose | 2 | 0.0% | 0.0% | — |
| cannot_pay | 4 | 0.0% | 0.0% | 0.0% |
| closing | 2 | 0.0% | 0.0% | — |
| deny | 6 | 0.0% | 0.0% | — |
| discount | 2 | 0.0% | 0.0% | — |
| dispute | 2 | 0.0% | 0.0% | — |
| dob_at_greeting | 5 | 0.0% | 0.0% | 0.0% |
| dob_full | 8 | 0.0% | 0.0% | 0.0% |
| dob_partial | 8 | 0.0% | 0.0% | 0.0% |
| dob_phase_number | 5 | 0.0% | 0.0% | — |
| dob_split_year | 8 | 0.0% | 0.0% | 0.0% |
| injection | 2 | 0.0% | 0.0% | 0.0% |
| multi_intent | 3 | 0.0% | 0.0% | 0.0% |
| noisy_stt | 2 | 0.0% | 0.0% | — |
| pay_amount | 8 | 0.0% | 0.0% | 0.0% |
| pay_amount_date | 8 | 0.0% | 0.0% | 0.0% |
| pay_correction | 5 | 0.0% | 0.0% | 0.0% |
| pay_date_only | 5 | 0.0% | 0.0% | 0.0% |
| request_human | 8 | 0.0% | 0.0% | — |
| stop_contact | 8 | 0.0% | 0.0% | — |
| unclear | 3 | 0.0% | 0.0% | — |
| wrong_person | 3 | 0.0% | 0.0% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 0.0% | 0.0% | 0.0% |
| ja | 44 | 0.0% | 0.0% | 0.0% |

### google/gemma-2b-it + training/adapter [local raw]

n=117 · valid 100.0% · action match **93.2%** · exact match **90.6%** · slot match 94.9% · caller-rights recall 94.1% (GATE FAILED) · latency p50 1346.0 ms / p95 1680.7 ms

| category | n | action | exact | slots |
|---|---:|---:|---:|---:|
| affirm | 8 | 100.0% | 100.0% | — |
| ask_balance | 2 | 100.0% | 100.0% | — |
| ask_purpose | 2 | 100.0% | 100.0% | — |
| cannot_pay | 4 | 75.0% | 75.0% | 100.0% |
| closing | 2 | 50.0% | 50.0% | — |
| deny | 6 | 83.3% | 83.3% | — |
| discount | 2 | 50.0% | 50.0% | — |
| dispute | 2 | 0.0% | 0.0% | — |
| dob_at_greeting | 5 | 100.0% | 100.0% | 100.0% |
| dob_full | 8 | 100.0% | 87.5% | 87.5% |
| dob_partial | 8 | 100.0% | 100.0% | 100.0% |
| dob_phase_number | 5 | 100.0% | 100.0% | — |
| dob_split_year | 8 | 100.0% | 87.5% | 87.5% |
| injection | 2 | 100.0% | 100.0% | 100.0% |
| multi_intent | 3 | 66.7% | 66.7% | 100.0% |
| noisy_stt | 2 | 100.0% | 100.0% | — |
| pay_amount | 8 | 100.0% | 100.0% | 100.0% |
| pay_amount_date | 8 | 100.0% | 100.0% | 100.0% |
| pay_correction | 5 | 100.0% | 80.0% | 80.0% |
| pay_date_only | 5 | 100.0% | 100.0% | 100.0% |
| request_human | 8 | 87.5% | 87.5% | — |
| stop_contact | 8 | 100.0% | 100.0% | — |
| unclear | 3 | 100.0% | 100.0% | — |
| wrong_person | 3 | 100.0% | 100.0% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 94.5% | 93.2% | 97.4% |
| ja | 44 | 90.9% | 86.4% | 90.0% |

