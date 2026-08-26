# CryptoEdge v19.25.11

Sprzątanie martwych kawałków z audytu.

## `fib_confluence.py`
Moduł nic nie robił — re-export z `reversal_engine` + `build_fib_context` / `asymmetry_ok` bez żadnego callera.
Usunięty. Fibo zostaje tam, gdzie żyje: V2 `swing_structure`, reversal `fibonacci_map`. UI confluence bez zmian (payload sygnału).

## LIVE reversal
`REVERSAL_LIVE_EXECUTION_ENABLED` — zbędna druga bramka obok `LIVE_EXECUTION_ENABLED` + `PAPER_TRADING`.
Reversal jest **tylko PAPER**. LIVE zawsze `REVERSAL_LIVE_DISABLED`, nawet gdy `LIVE_EXECUTION_ENABLED=True`.
Zostaje `REVERSAL_PAPER_EXECUTION_ENABLED` / `REVERSAL_SHADOW_ONLY`.

## CONTROL_*
`CONTROL_PAUSE_FILE` / `_CLOSE_ALL` / `_RESUME` były tylko aliasami na `"PAUSE"` / `"CLOSE_ALL"` / `"RESUME"`.
Drop-file w folderze bota działa dalej — nazwy na sztywno, bez flag.
