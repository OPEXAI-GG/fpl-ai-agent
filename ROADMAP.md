# Product roadmap

The goal is a subscription FPL platform that helps managers improve their teams and ranks
through dependable forecasts, personalized planning, and understandable recommendations.
This is a direction document, not a list of completed features or delivery dates.

## 1. Reliable decision engine — current focus

- Maintain source quality, identity resolution, and reproducible forecast inputs.
- Validate projections and planner decisions against chronological baselines.
- Collect matched forward evidence before promoting optional model features.
- Keep weekly learning bounded, inspectable, and reversible.

The database, CLI, planner, evaluation, and automation are implemented. The workload overlay
remains in shadow because the current historical evaluation has not established an accuracy gain.

## 2. A useful manager experience

- Make recommendations easy to understand and apply near a deadline.
- Explain transfer costs, alternatives, captaincy, chips, uncertainty, and data freshness.
- Support reliable personalization around a manager's existing team and objectives.
- Learn from usage and feedback while measuring the quality of decisions.

## 3. Customer-facing web product — planned

- Provide accounts, onboarding, a squad view, and a weekly recommendation experience.
- Expose the engine through a stable service interface.
- Isolate customer data and enforce access boundaries on the server.
- Add monitoring, support tools, and clear operational recovery paths.

## 4. Subscription launch — planned

- Implement checkout, server-side entitlements, and reliable billing-event handling.
- Validate the value offered to paying managers and the cost of serving them.
- Measure activation, retention, reliability, and sustainable revenue.
- Publish accurate performance evidence and clear product limitations.

Future work should be scoped as concrete, reviewable milestones. Website and billing work
does not imply enabling automated FPL transfers; execution authorization remains a separate decision.
