# What is wrong with `data/feed.jsonl` (measured on 2,518 lines)

Re-measure with your own profile script (prompt 02); the numbers below are what to expect.

| # | Problem | Count | Rule |
|---|---|---|---|
| 1 | Truncated JSON (`..."updatedAt": "2026-08-14T09:15:13+0`) | 5 | reject `invalid_json`, keep raw |
| 2 | Bare `null` line | 6 | reject `not_an_object` |
| 3 | No `registrationNumber` (`"No Reg Sdn. Bhd."`) | 7 | reject `missing_registration_number` |
| 4 | `registrationNumber` is an int (`119`, name `"X"`, `"Trading"`) | 3 | reject `invalid_registration_number` |
| 5 | **Second event type `company-struck-off`**, no `data` | 8 | status change, not a delete |
| 6 | Reg number case/space (`syn-000587`, ` SYN-001497 `): 2,049 raw strings -> 2,000 companies | 49 | `strip().upper()` |
| 7 | `updatedAt`: ISO `+08:00` 2,428 / `Z` 28 / no zone 14 / epoch ms 30 / missing 7 | | one parser, return UTC |
| 8 | `data` missing 13, or a **string** (`"companyName=Foo; state=Perak"`) 4 | 17 | keep the company, event carries no fields, record a warning |
| 9 | Exact duplicates, incl. same record in different timestamp formats | 63 | one row per `(registration_number, content hash)` |
| 10 | `source` vs `ingestedBy` key (`ssm-batch`, `partner-api`, `manual-entry`) | 38 / 37 | unify |
| 11 | `state` casing (`PERAK`/`perak`/`Perak`); conflicts with description (Seremban hotel listed under Selangor) | many | canonical Title Case; do not trust; do not embed |
| 12 | `natureOfBusiness` null or `""` | 51 | no overwrite; fallback `industry + name`, else no embedding |
| 13 | Runaway repetition in `natureOfBusiness`, up to 3,359 chars | 7 long | collapse repeated phrase, cap at the IQR upper fence of lengths (expect ~512; verify), add a warning |
| 14 | Companies with >1 distinct `data` payload (real updates) | 126 | latest wins |
| 15 | Out-of-order timestamps within the file | yes | never use arrival order |
| 16 | Only **~172 distinct descriptions** for 2,000 companies; a text is shared by up to ~43 companies; every business type exists in **English and Malay** ("Boutique hotel in Ipoh" / "Hotel butik di Ipoh") | | many exact ties; an English-only model will under-match Malay: this is your deep-dive material |
| 17 | Name contradicts data ("Teguh Teguh Pharmacy" = home nursing) | some | trust the description, do not embed the name |

## Expected end state after a correct run
2,000 companies; 8 struck off; 4 with no embedding at all (no description and no industry); ~35 embedded from the
industry+name fallback; ~2,434 distinct stored events; 21 unattributable lines (5+6+7+3) in a rejects table.
Only ~200 distinct texts ever need embedding.

## Traps this implies
- A schema with `NOT NULL` on description/industry/state will crash or drop rows.
- "Update only if newer" on a single row is not order-independent once you also merge nulls (see 03-architecture).
- `message_id` is the SHA-256 of the raw line: identical lines share it, but the same record with another timestamp format does not.
- Requeueing a poison message loops forever; the queue has no DLX and you cannot add one.

## Data-preparation rules (decided)
Timestamps without offset = +08:00. Newer null/empty never erases a value. Outliers (description length by IQR, timestamps outside a
sane window) are flagged with a warning and kept; nothing is dropped except the unattributable lines in the reject table.
