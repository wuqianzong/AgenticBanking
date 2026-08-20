# Direct-`None` Manual Corpus Audit v1

## Scope and method

This is a manual, evidence-based audit of all **16** cases whose direct
retrieval evidence set was previously judged `none` in
`threeway_narrative_validation_v1.json`. It is not an additional LLM run.

For each case, the audit checked the direct parents, all parents returned by
the saved narrative strategies, and lexical matches in the complete Horizon
parent corpus. A clear denial or corrective alternative counts as an answer;
for example, “Horizon does not support cross-border transfers” answers a
request for an international-transfer timeline.

## Corrected classifications

| Case | Query issue | Classification | Supporting parent(s) | Finding |
|---|---|---|---|---|
| `activate_my_card_10` | “bill of fare … didn’t workplace” | Clarification/noise | — | The key object is not a recognisable Horizon product or task. |
| `change_pin_01` | Which cash machines allow PIN changes? | Supported by direct | `faq_6dae126c` | The FAQ gives the supported alternative: change the PIN in the app. |
| `change_pin_05` | Do cash machines let me change my PIN? | Supported by direct | `faq_6dae126c` | Same app-based PIN-change answer. |
| `change_pin_06` | Change “Tholepin” | Clarification/noise | — | “Tholepin” is undefined. |
| `card_arrival_04` | Track sent card | Retrieval/scope failure | `faq_7e20cb52` | Exact card-delivery-status FAQ exists but was not in direct top results. |
| `card_arrival_06` | Tracking info available? | Retrieval/scope failure | `faq_7e20cb52` | Same exact tracking FAQ; its taxonomy is Transaction Disputes, not the supplied Card Logistics scope. |
| `lost_or_stolen_card_03` | Is a police report necessary? | Supported by direct | `faq_7883a8c2` | It says the customer *may* file a police report, so it is not stated as required. |
| `transfer_timing_02` | Transfer from the US | Supported by direct | `faq_32c1662f` | Horizon supports domestic SGD transfers only and does not handle cross-border payments. |
| `transfer_timing_05` | Funds from US to account | Retrieval/ranking failure | `faq_32c1662f` | Same decisive no-cross-border answer exists but direct did not return it. |
| `transfer_timing_10` | Days for US transfer | Supported by direct | `faq_32c1662f` | Same decisive denial answer was retrieved. |
| `cancel_transfer_06` | “go back on what I did this morning” | Clarification-needed | — | The action is unspecified; transfer, AutoPay, and other reversal paths differ. |
| `transfer_into_account_07` | Move money from another bank into this one | Supported by direct | `faq_44bb339e` | It directly describes GIRO funding from another bank into Horizon. |
| `transfer_into_account_08` | Transfer “in order to Top up” | Clarification-needed | — | Destination/product and transfer type are not sufficiently clear. |
| `transfer_into_account_09` | “dual money transferring” | Clarification/noise | — | The requested operation is undefined. |
| `passcode_forgotten_01` | Forgotten password | Clarification-needed | — | The corpus supports forgotten *app passcode* and Horizon Key PIN, not a generic password. |
| `passcode_forgotten_03` | Cannot find password | Clarification-needed | — | Same password-versus-passcode ambiguity. |

## Result

| Corrected outcome | Count |
|---|---:|
| Direct retrieval already sufficient | 6 |
| Retrieval or taxonomy-scope failure | 3 |
| Clarification/noisy input | 7 |
| Confirmed corpus gap | 0 |

## Implications

The old `none` outcome mixed four qualitatively different states. Most
notably, the prior judge incorrectly rejected direct evidence that either
provided the supported alternative (PIN changes) or explicitly denied the
requested unsupported capability (US transfers).

Only three cases are suitable recovery-retrieval research examples. Two card
tracking questions expose a taxonomy error: the exact tracking FAQ is tagged
`Transaction Disputes` rather than Card Logistics. One US-transfer question
missed an otherwise decisive cross-border-support FAQ. These are scope/ranking
problems, not evidence for a narrative angle.
