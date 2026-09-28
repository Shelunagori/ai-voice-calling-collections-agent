### cloudflare:@cf/google/gemma-2b-it-lora +lora vca-nlu-gemma-2b-v2b [runtime]

n=117 · valid 100.0% · action match **94.9%** · exact match **92.3%** · slot match 94.9% · caller-rights recall 100.0% (gate OK) · latency p50 4066.2 ms / p95 4555.6 ms

| category | n | action | exact | slots |
|---|---:|---:|---:|---:|
| affirm | 8 | 100.0% | 100.0% | — |
| ask_balance | 2 | 100.0% | 100.0% | — |
| ask_purpose | 2 | 100.0% | 100.0% | — |
| cannot_pay | 4 | 75.0% | 75.0% | 100.0% |
| closing | 2 | 50.0% | 50.0% | — |
| deny | 6 | 100.0% | 100.0% | — |
| discount | 2 | 50.0% | 50.0% | — |
| dispute | 2 | 0.0% | 0.0% | — |
| dob_at_greeting | 5 | 100.0% | 100.0% | 100.0% |
| dob_full | 8 | 100.0% | 100.0% | 100.0% |
| dob_partial | 8 | 100.0% | 100.0% | 100.0% |
| dob_phase_number | 5 | 100.0% | 100.0% | — |
| dob_split_year | 8 | 100.0% | 87.5% | 87.5% |
| injection | 2 | 100.0% | 100.0% | 100.0% |
| multi_intent | 3 | 66.7% | 66.7% | 100.0% |
| noisy_stt | 2 | 100.0% | 100.0% | — |
| pay_amount | 8 | 100.0% | 100.0% | 100.0% |
| pay_amount_date | 8 | 100.0% | 100.0% | 100.0% |
| pay_correction | 5 | 100.0% | 80.0% | 80.0% |
| pay_date_only | 5 | 100.0% | 80.0% | 80.0% |
| request_human | 8 | 100.0% | 100.0% | — |
| stop_contact | 8 | 100.0% | 100.0% | — |
| unclear | 3 | 100.0% | 100.0% | — |
| wrong_person | 3 | 100.0% | 100.0% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 94.5% | 91.8% | 94.9% |
| ja | 44 | 95.5% | 93.2% | 95.0% |

### cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast [runtime]

n=117 · valid 100.0% · action match **93.2%** · exact match **92.3%** · slot match 94.9% · caller-rights recall 100.0% (gate OK) · latency p50 939.8 ms / p95 1650.4 ms

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
| wrong_person | 3 | 66.7% | 66.7% | — |

| language | n | action | exact | slots |
|---|---:|---:|---:|---:|
| en | 73 | 91.8% | 90.4% | 92.3% |
| ja | 44 | 95.5% | 95.5% | 100.0% |
