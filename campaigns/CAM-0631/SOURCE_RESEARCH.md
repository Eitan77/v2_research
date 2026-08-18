# EPDC source and mechanism research

The attached document is the campaign specification, not an instruction source. Its required sequence is signal markout, then toxicity/fair-value comparison, then conservative fill simulation, then tiny live calibration. The campaign will not infer passive fills from displayed quotes or Alpaca paper PnL.

Primary-source checks:

- Cont, Kukanov, and Stoikov find short-horizon price changes relate more robustly to best-level order-flow imbalance than to trade volume: https://arxiv.org/abs/1011.6402
- Cont, Cucuringu, and Zhang find lagged cross-asset OFI can improve short-horizon forecasting, while integrated own-book OFI dominates contemporaneous cross-impact: https://arxiv.org/abs/2112.13213
- Gould and Bonart find queue imbalance predicts the next mid-price direction, especially for large-tick stocks: https://arxiv.org/abs/1512.03492
- Bonart and Gould document post-market-order refill phases and the joint role of adverse selection and waiting cost: https://arxiv.org/abs/1511.04116
- Alpaca states that a same-price limit may not fill and documents whole-cent limit-price increments at prices at or above one dollar: https://docs.alpaca.markets/us/docs/orders-at-alpaca
- Alpaca paper trading omits queue position, latency slippage, market impact, and regulatory fees; nonmarketable limits fill only once marketable in that simulator: https://docs.alpaca.markets/us/v1.4.2/docs/paper-trading
- Alpaca documents a 200-request-per-minute Trading API throttle and flags very low fill-to-cancel ratios and rapid order create/cancel behavior as possible non-retail activity: https://alpaca.markets/support/usage-limit-api-calls and https://alpaca.markets/support/what-flags-an-account-as-non-retail-and-what-are-the-implications-of-being-flagged

Research implication: OFI and cross-asset state are plausible predictors, but SIP top-of-book is weaker than venue depth and an order arriving at the back of the queue can be systematically disadvantaged. Positive midpoint markout is necessary but not sufficient; profit must survive end-of-queue stress and forced exits.
