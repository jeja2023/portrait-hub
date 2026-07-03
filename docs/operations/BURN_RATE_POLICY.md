# Burn Rate Policy

PortraitHub uses two operational windows:

- Fast burn: short-window error rate above 2% means page immediately.
- Slow burn: one-hour error rate above 1% means create a ticket and watch capacity.

Error budget guidance:

- Stop risky rollouts when error budget consumption trends up for more than one review cycle.
- Revisit thresholds and traffic splitting when burn-rate alerts trigger repeatedly.
