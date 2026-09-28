### cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast [raw]

n=117 · valid 100.0% · action match **89.7%** · exact match **88.0%** · slot match 91.5% · caller-rights recall 100.0% (gate OK) · latency p50 1005.5 ms / p95 4222.1 ms

| category | n | action | exact | slots |
|---|---:|---:|---:|---:|
| affirm | 8 | 100.0% | 100.0% | — |
| ask_balance | 2 | 100.0% | 100.0% | — |
| ask_purpose | 2 | 0.0% | 0.0% | — |
| cannot_pay | 4 | 50.0% | 50.0% | 100.0% |
| closing | 2 | 100.0% | 100.0% | — |
| deny | 6 | 100.0% | 100.0% | — |
| discount | 2 | 100.0% | 100.0% | — |
| dispute | 2 | 100.0% | 100.0% | — |
| dob_at_greeting | 5 | 100.0% | 100.0% | 100.0% |
| dob_full | 8 | 100.0% | 100.0% | 100.0% |
| dob_partial | 8 | 87.5% | 75.0% | 75.0% |
| dob_phase_number | 5 | 40.0% | 40.0% | — |
| dob_split_year | 8 | 100.0% | 100.0% | 100.0% |
| injection | 2 | 50.0% | 50.0% | 100.0% |
| multi_intent | 3 | 66.7% | 66.7% | 100.0% |
| noisy_stt | 2 | 100.0% | 100.0% | — |
| pay_amount | 8 | 100.0% | 100.0% | 100.0% |
| pay_amount_date | 8 | 100.0% | 87.5% | 87.5% |
| pay_correction | 5 | 60.0% | 60.0% | 60.0% |
| pay_date_only | 5 | 100.0% | 100.0% | 100.0% |
| request_human | 8 | 100.0% | 100.0% | — |
| stop_contact | 8 | 100.0% | 100.0% | — |
| unclear | 3 | 100.0% | 100.0% | — |
| wrong_person | 3 | 100.0% | 100.0% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 87.7% | 86.3% | 89.7% |
| ja | 44 | 93.2% | 90.9% | 95.0% |

### cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast [runtime]

n=117 · valid 100.0% · action match **91.5%** · exact match **86.3%** · slot match 81.4% · caller-rights recall 100.0% (gate OK) · latency p50 907.3 ms / p95 2251.8 ms

| category | n | action | exact | slots |
|---|---:|---:|---:|---:|
| affirm | 8 | 100.0% | 100.0% | — |
| ask_balance | 2 | 100.0% | 100.0% | — |
| ask_purpose | 2 | 0.0% | 0.0% | — |
| cannot_pay | 4 | 75.0% | 75.0% | 100.0% |
| closing | 2 | 100.0% | 100.0% | — |
| deny | 6 | 100.0% | 100.0% | — |
| discount | 2 | 100.0% | 100.0% | — |
| dispute | 2 | 100.0% | 100.0% | — |
| dob_at_greeting | 5 | 100.0% | 100.0% | 100.0% |
| dob_full | 8 | 100.0% | 100.0% | 100.0% |
| dob_partial | 8 | 100.0% | 100.0% | 100.0% |
| dob_phase_number | 5 | 100.0% | 100.0% | — |
| dob_split_year | 8 | 62.5% | 12.5% | 12.5% |
| injection | 2 | 50.0% | 50.0% | 100.0% |
| multi_intent | 3 | 66.7% | 66.7% | 100.0% |
| noisy_stt | 2 | 100.0% | 100.0% | — |
| pay_amount | 8 | 100.0% | 87.5% | 87.5% |
| pay_amount_date | 8 | 100.0% | 87.5% | 87.5% |
| pay_correction | 5 | 60.0% | 60.0% | 60.0% |
| pay_date_only | 5 | 100.0% | 100.0% | 100.0% |
| request_human | 8 | 100.0% | 100.0% | — |
| stop_contact | 8 | 100.0% | 100.0% | — |
| unclear | 3 | 100.0% | 100.0% | — |
| wrong_person | 3 | 100.0% | 100.0% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 89.0% | 80.8% | 71.8% |
| ja | 44 | 95.5% | 95.5% | 100.0% |

### rules [raw]

n=117 · valid 100.0% · action match **93.2%** · exact match **88.9%** · slot match 86.4% · caller-rights recall 100.0% (gate OK) · latency p50 0.0 ms / p95 0.4 ms

| category | n | action | exact | slots |
|---|---:|---:|---:|---:|
| affirm | 8 | 100.0% | 100.0% | — |
| ask_balance | 2 | 100.0% | 100.0% | — |
| ask_purpose | 2 | 100.0% | 100.0% | — |
| cannot_pay | 4 | 75.0% | 75.0% | 100.0% |
| closing | 2 | 100.0% | 100.0% | — |
| deny | 6 | 100.0% | 100.0% | — |
| discount | 2 | 50.0% | 50.0% | — |
| dispute | 2 | 100.0% | 100.0% | — |
| dob_at_greeting | 5 | 100.0% | 100.0% | 100.0% |
| dob_full | 8 | 100.0% | 100.0% | 100.0% |
| dob_partial | 8 | 100.0% | 100.0% | 100.0% |
| dob_phase_number | 5 | 100.0% | 100.0% | — |
| dob_split_year | 8 | 62.5% | 12.5% | 12.5% |
| injection | 2 | 50.0% | 50.0% | 100.0% |
| multi_intent | 3 | 66.7% | 66.7% | 100.0% |
| noisy_stt | 2 | 100.0% | 100.0% | — |
| pay_amount | 8 | 100.0% | 87.5% | 87.5% |
| pay_amount_date | 8 | 100.0% | 100.0% | 100.0% |
| pay_correction | 5 | 100.0% | 100.0% | 100.0% |
| pay_date_only | 5 | 100.0% | 100.0% | 100.0% |
| request_human | 8 | 100.0% | 100.0% | — |
| stop_contact | 8 | 100.0% | 100.0% | — |
| unclear | 3 | 100.0% | 100.0% | — |
| wrong_person | 3 | 66.7% | 66.7% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 91.8% | 84.9% | 79.5% |
| ja | 44 | 95.5% | 95.5% | 100.0% |

