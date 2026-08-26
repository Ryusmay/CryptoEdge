# CryptoEdge v19.25.12

Warmup 5 min na tej samej bramce V2 co `generate_signals`.

Do 19.25.11 pętla w `app.py` startowała warmup tylko przy `DAYTRADING_V2_ENABLED`.
`STRATEGY_MODE=DAYTRADING_V2` bez tej flagi puszczało V2 od razu — bez 5 min
backfillu świec. Test A/B (`test_strategy_mode_daytrading_v2_alone_is_sufficient`)
dokładnie tak patchuje.

Teraz jedno miejsce: `config.daytrading_v2_active()` = flaga **lub** MODE=V2.
Warmup: `warmup_applies()` = `WARMUP_ENABLED` i V2 aktywne.
V1 (`MODE=DAYTRADING` + flaga off) nadal bez warmup.
