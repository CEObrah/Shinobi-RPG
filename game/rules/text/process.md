# Autonomous Processes and Temporal Settlement

`RUNTIME.md` owns the transaction and full catch-up procedure. `runtime/contracts/temporal-settlement.json` owns recurrence, batching, residual accrual, successor-plan, trigger, closure, and player-boundary mechanics. `state/time/causal-scheduler.json` owns the current causal host/event frontier.

Every evolving owner must have direct or aggregate process coverage. A due process is not considered caught up after only one overdue cycle. Settlement continues through every due boundary and, for continuous accrual, through the exact reached world time.

Keep three boundary kinds distinct:

- **Internal causal boundary:** training, recovery, mission progress, movement, economy, force/formation work, faction/institution review, population work, or other persistent simulation that creates no new player-facing situation. Resolve it and continue. It never creates `decision_required` merely because bookkeeping happened.
- **Soft player-facing event:** a real report, mission offer, team contact, observable pressure, public consequence, institutional transition, or similar development that ChatGPT should render as a scene. Event-seeking downtime may stop here, but the event does not itself choose for Wei and does not close time passage unless a separate protected decision is present.
- **Hard player decision:** a consequential protected choice that standing orders, delegation, or deterministic mechanics cannot supply. This is the only ordinary temporal boundary that sets `decision_required` and closes automatic time passage.

Player-required consequential decisions are hard interrupts unless saved standing orders or delegation lawfully resolve them. Player-visible information is not automatically a decision, and internal causal work is never a story stop.
