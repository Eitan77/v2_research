# V2 Reference Code

This directory contains copied source code and tests that may be useful to V2 campaigns. It is reference material, not an approved strategy library. The copied code may be adapted or replaced when a campaign requires it.

## Provenance

- `ar_pipeline/` comes from `D:\AlgoResearch\src\ar_pipeline` and contains reusable bar-fill, quote-fill, portfolio-validation, trade-audit, robustness, data, execution, and CUDA components.
- `quant_pipeline_base/` comes from `D:\AlgoResearch\src\quant_pipeline`.
- `quant_pipeline_existing/` comes from `D:\AlgoResearch\Quant Pipeline\src\quant_pipeline` and is a separate specialized pipeline.
- `tools/` comes from `D:\AlgoResearch\tools` and contains standalone research and CUDA scripts.
- `quant_pipeline_tools/` comes from `D:\AlgoResearch\Quant Pipeline\tools`.
- `tests_base/` and `tests_existing/` contain copied tests for reference when adapting code.

Only source code and tests were copied. Results, reports, runs, caches, credentials, and generated outputs were deliberately excluded.

Do not treat code names, comments, or old tests as evidence for a strategy conclusion. Verify every reused component against the campaign's data contract, timing, costs, and holdout boundary.
