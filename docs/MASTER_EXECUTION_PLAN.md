# FINSENTIMIND — MASTER PRODUCT EXECUTION PROMPT
## From Research MVP to Complete Personal Investment Intelligence Workstation

PROJECT: FinSentiMind
PRODUCT CATEGORY: Quantitative Equity Research & Portfolio Intelligence
PRIMARY MARKET: Indonesia Stock Exchange (IDX / BEI)
SECONDARY MARKET: Global macro and cross-asset indicators

---

# PART 0 — YOUR ROLE AND MISSION

You are the principal software architect, senior quantitative developer, data engineer, financial modeling engineer, and technical lead responsible for completing FinSentiMind.

Your mission is to develop the existing FinSentiMind research MVP into a comprehensive personal investment research and portfolio monitoring workstation.

This is a continuation of an existing project.

You MUST inspect and preserve the existing implementation. Do not rebuild completed components without a demonstrated technical reason.

The owner wants to learn quantitative finance through the application. Therefore, financial models must be explainable, auditable, and educational.

The application should not merely display numbers. It must explain:

- What a metric means.
- How it is calculated.
- Which formula is used.
- What data is required.
- What assumptions are made.
- What the result means.
- What its limitations are.
- When the result should not be trusted.

The final goal is TASK 121 — COMPLETE PERSONAL RESEARCH & PORTFOLIO WORKSTATION ACCEPTANCE.

Do not stop after TASK 107A.

Execute milestones sequentially, preserve completed work, and continue through TASK 121 unless a genuine external-approval requirement or technical blocker prevents safe execution.

---

# PART 1 — CURRENT PROJECT CONTEXT

Repository:

D:\Projekan kaga jadijadi anj\API-project-saham

Current completion branch:

feat/finsentimind-completion

Last reported commit:

6d58f99

Existing completed milestones:

- TASK 107: Refresh reliability and audit remediation.
- TASK 108: Instrument intelligence and metadata.
- TASK 109: Quantitative feature engine.
- TASK 110: Market intelligence.
- TASK 111: Leakage-safe ML research.
- TASK 112: Hybrid terminal redesign.

Last reported test result:

398 passed, 5 pre-existing Pydantic deprecation warnings.

Existing architecture:

Backend:
- Python
- FastAPI
- Supabase
- Local Parquet datasets

Frontend:
- Vanilla JavaScript
- HTML
- CSS
- Custom SVG charts

Data architecture:
- Immutable dataset generations.
- SHA-256 manifests.
- Atomic active.json pointer.
- Legacy flat-file fallback.
- Staging and validation.
- Refresh locking and rollback.

Existing ML research:
- Walk-forward validation.
- Embargo.
- Logistic regression baseline.
- Dummy baseline.
- Research metrics and explainability.
- NOT_VALIDATED status.
- not_for_trading=true.

Existing financial features:
- Returns.
- SMA/EMA.
- RSI.
- ATR.
- Volatility.
- Relative volume.
- Market breadth.
- Sector performance.
- Market regime.

Existing terminal:
- Market Overview.
- Market Monitor.
- Stock Explorer.
- Stock Detail.
- Watchlist.
- Data Health.
- Command Palette.

IMPORTANT:

These are completed according to the previous engineering report. Verify the actual repository before relying on any assumption.

---

# PART 2 — PRODUCT VISION (LOCKED)

## Product identity

FinSentiMind is a hybrid investment intelligence workstation.

It combines:

1. Bloomberg-style market monitoring and information density.
2. Modern quantitative research and analytics.
3. Fundamental value investing research.
4. Capital flow intelligence.
5. Portfolio management and risk analysis.

The product must not become a generic SaaS dashboard.

## Equal product priorities

Priority A: Market Monitoring.

Priority B: Investment Research.

Neither is subordinate to the other.

## Initial users

Single personal user / project owner.

## Future users

Public users after licensing, compliance, security, and infrastructure requirements have been addressed.

## Trading scope

Read-only research and monitoring.

No live order execution, broker order routing, automated trading, or custody of user funds.

Do not implement those features in this roadmap.

## Locked decisions

Capital Flow = BOTH:
- Market-wide capital flow.
- Individual stock-level capital flow.

Portfolio = BOTH:
- Personal portfolio tracking.
- Model / virtual portfolio tracking.

Product experience = HYBRID:
- Research workstation.
- Market monitoring terminal.

---

# PART 3 — NON-NEGOTIABLE ENGINEERING PRINCIPLES

1. No look-ahead bias.
2. No fabricated financial data.
3. No fabricated provider responses.
4. Missing data must remain distinguishable from zero.
5. Every financial metric must have a documented formula.
6. Every external data point must have provenance.
7. Every dataset must have an as-of timestamp.
8. Historical values must be reproducible.
9. No silent data truncation.
10. No silent failure.
11. No unjustified changes to existing API contracts.
12. Preserve legacy compatibility where practical.
13. Every milestone must have regression tests.
14. Every important calculation must have numerical tests.
15. Never label an unvalidated model as a trusted signal.

If a data provider is unavailable or licensing is uncertain:

- Build the adapter interface.
- Define the expected schema.
- Return explicit unavailable status.
- Document the limitation.
- Use mocked fixtures for testing.
- Do not fabricate real-looking data.

## External mutation policy

Code commits and pushes are allowed to dedicated feature branches after tests and review.

The following require explicit owner approval:

- Supabase writes and historical backfills.
- Dataset generation publication.
- active.json changes.
- Paid provider subscriptions.
- Provider account creation.
- External data redistribution.
- Live broker connections.
- Any real order execution.
- Production deployment or public release.

Never merge into main without explicit authorization.

Never force push or rewrite history.

Never commit:
- .env
- Secrets
- Credentials
- Parquet datasets
- Private database dumps

---

# PART 4 — EXECUTION ROADMAP

Complete the following milestones sequentially.

## TASK 107A — Refresh Edge-Case Reliability

Branch:

feat/task-107a-refresh-edge-cases

Base:

feat/finsentimind-completion at 6d58f99.

### A. Future-session handling

Failed run:

20261114T154144Z_acedb3bb

Failure:

No trading date found within 30 days after 2026-11-14 22:44 WIB.

Implement explicit article mapping outcomes:

- MAPPED
- PENDING_FUTURE_SESSION
- QUARANTINED_UNRESOLVABLE

Requirements:

1. Future articles must not crash aggregation merely because the next session is unknown.
2. Never map future news to the previous trading session.
3. Never fabricate future sessions through generic weekday logic.
4. Preserve pending article metadata.
5. Reprocess pending articles when a later verified calendar becomes available.
6. Preserve Asia/Jakarta timezone and SIGNAL_CUTOFF semantics.
7. Preserve effective_available_at = max(published_at, scraped_at).
8. Preserve no-look-ahead behavior.
9. Historical malformed articles must receive explicit quarantine reasons.
10. All-pending input may produce a valid empty daily sentiment dataset with the existing schema.

Produce:

ml/reports/pending_news_<run_id>.json

Include article ID, ticker, timestamps, pending reason, and reprocessing eligibility.

### B. GOTO volume anomaly

Investigate:

GOTO, 2026-10-09.

Previous volume:

32,994,912

New volume:

12,917,896,800

Inspect:
- Original Supabase record.
- yfinance raw response.
- Backfill normalization.
- Upsert payload.
- Cleaned Parquet.
- Volume units.
- Adjustment behavior.

Never guess or arbitrarily normalize volume.

If the cause cannot be proven, document the unresolved risk.

### C. Calendar gap audit

Investigate all 822 missing weekday groups.

Classify:
- Official IDX holidays.
- Collective leave.
- Genuine missing observations.
- IPO/listing coverage limitations.
- Unexplained gaps.

Produce:

ml/reports/calendar_gap_audit.md

Use reliable evidence. Do not automatically classify every weekday gap as an exchange holiday.

### D. Tests

Add regression tests for pending future news, reprocessing, malformed historical timestamps, empty aggregation, timezone handling, volume anomalies, and calendar classification.

Preserve:

ml/data/staging/20261114T154144Z_acedb3bb/

No full provider refresh or publication.

Commit and push the feature branch.

---

## TASK 107B — Full Refresh Acceptance & Data Generation Activation

Objective:

Prove that the refresh pipeline can safely produce a new complete dataset generation.

### Requirements

1. Inspect TASK 107A results.
2. Verify every upstream dataset.
3. Run validate-only.
4. Run dry-run.
5. Verify price coverage and quality.
6. Verify calendar coverage.
7. Verify pending news handling.
8. Verify daily sentiment.
9. Verify base dataset.
10. Verify quant feature generation.
11. Verify full dataset validation.
12. Verify generation manifest and checksums.
13. Verify rollback.
14. Verify active reader behavior.

### Publication gate

A real provider refresh and generation publication may only occur after explicit owner approval.

Before approval:
- Do not write to Supabase.
- Do not publish a generation.
- Do not change active.json.
- Do not perform historical backfills.

Prepare a controlled execution plan and report what would be changed.

If approval is not available, complete all local/mock/dry-run verification and mark real publication as pending.

The rest of the roadmap may continue using verified local fixtures and existing data.

Acceptance:

The old dataset must remain intact if any stage fails.

---

## TASK 113 — Fundamental Data Foundation

Objective:

Create a reliable fundamental-data architecture for IDX companies.

### Data coverage

Annual and quarterly:

- Income statement.
- Balance sheet.
- Cash flow statement.
- Corporate actions.
- Dividend history.
- Shares outstanding.
- Earnings per share.
- Book value.
- Financial reporting periods.

### Data architecture

Create a normalized and auditable fundamental data layer.

Suggested modules:

ml/fundamentals/
    schemas.py
    ingestion.py
    normalization.py
    quality.py
    repository.py
    metrics.py

Suggested datasets:

financial_statements
fundamental_metrics
corporate_actions
dividend_history

Actual schema must follow verified sources and existing project conventions.

### Requirements

- Preserve original units and normalized units.
- Distinguish annual and quarterly data.
- Distinguish consolidated and standalone statements.
- Preserve fiscal periods.
- Preserve publication timestamps.
- Preserve source provenance.
- Track restatements and corrections.
- Prevent duplicate financial periods.
- Handle missing values without converting them to zero.
- Detect inconsistent accounting units.
- Preserve historical point-in-time availability.

### Data source policy

First create a provider/source inventory.

Evaluate:
- Official IDX disclosures.
- Company financial statements.
- Authorized redistributors.
- Licensed financial data providers.
- Owner-provided documents.

Do not scrape restricted sources or violate provider terms.

If reliable automated access is unavailable, support documented manual import of source financial statements.

Do not invent missing fundamentals.

### Acceptance

- Verified normalized schema.
- Reproducible ingestion.
- Source provenance.
- Financial statement validation.
- Tests for missing periods, unit conversion, duplicate records, restatements, and timestamp availability.

---

## TASK 114 — Fundamental Analysis & Buffett-Style Valuation

Objective:

Build a complete fundamental research and valuation engine.

### A. Financial ratios

Implement and document:

Profitability:
- Gross margin.
- Operating margin.
- Net margin.
- ROE.
- ROA.
- ROIC.

Growth:
- Revenue growth.
- Earnings growth.
- EPS growth.
- FCF growth.

Leverage:
- Debt/equity.
- Net debt.
- Net debt/EBITDA.
- Interest coverage.

Cash flow:
- Operating cash flow.
- Free cash flow.
- Cash conversion.
- CFO/net income.

Valuation:
- P/E.
- P/B.
- EV/EBITDA.
- P/S.
- FCF yield.
- Dividend yield.

Every metric must include its exact formula, input fields, units, period, and limitations.

### B. Warren Buffett / Value Investing Framework

Implement an educational research framework based on:

1. Understandable business / circle of competence.
2. Business quality.
3. Competitive advantage / economic moat.
4. Consistent earnings.
5. Return on invested capital.
6. Cash generation.
7. Management and capital allocation.
8. Financial strength.
9. Intrinsic value.
10. Margin of safety.

Do not automatically fabricate qualitative moat or management scores.

Allow owner-entered research notes with supporting evidence.

### C. Owner earnings

Implement an auditable owner earnings estimate.

Conceptual formula:

Owner Earnings =
Net Income
+ Depreciation and Amortization
- Maintenance Capital Expenditure
- Additional Working Capital Requirements

IMPORTANT:

Maintenance capex is not necessarily equal to total capex.

Where maintenance capex cannot be identified from reported data, expose the assumption explicitly and allow scenario inputs.

### D. Discounted Cash Flow

Support:

- FCFF valuation.
- FCFE valuation.
- WACC.
- Cost of equity.
- Terminal value.
- Per-share intrinsic value.
- Sensitivity analysis.

Core enterprise DCF:

EV = PV(projected FCFF) + PV(Terminal Value)

Terminal Value:

TV = FCFF_n × (1 + g) / (WACC - g)

Equity Value:

Equity Value = Enterprise Value - Net Debt

Intrinsic Value Per Share:

Equity Value / Diluted Shares Outstanding

Reject invalid assumptions such as WACC <= terminal growth.

### E. Margin of safety

Margin of Safety:

(Intrinsic Value - Market Price) / Intrinsic Value

Show multiple scenarios:
- Bear.
- Base.
- Bull.

Never present a single intrinsic value as certain.

### Acceptance

Build a valuation report per ticker with:
- Financial quality.
- Growth.
- Balance sheet.
- Owner earnings.
- DCF scenarios.
- Margin of safety.
- Assumptions.
- Source citations.
- Missing-data warnings.

---

## TASK 115 — Capital Flow Intelligence

Objective:

Build comprehensive capital-flow analysis.

Capital Flow is BOTH:

1. Market-wide flow.
2. Individual stock-level flow.

### A. Market-wide indicators

Where reliable data exists:

- Foreign net buy/sell.
- Foreign gross buy/sell.
- Domestic institutional flows.
- Retail flows.
- Broker-level aggregates.
- Sector-level flows.
- Daily and weekly net flow.
- Flow concentration.
- Market-wide accumulation/distribution.

### B. Individual stock indicators

Where source data permits:

- Broker net buy/sell.
- Broker gross buy/sell.
- Foreign participation.
- Trading value.
- Trading volume.
- Average transaction value.
- Broker concentration.
- Flow persistence.
- Accumulation/distribution.
- Price versus net-flow divergence.

### C. Analytical features

Examples:

- Top net-buy brokers.
- Top net-sell brokers.
- Foreign flow trend.
- Sector capital rotation.
- Broker concentration ratio.
- Net flow / traded value.
- Multi-session accumulation.
- Price and volume confirmation.

### D. Critical interpretation boundary

Exchange transactions have both buyers and sellers.

Net flow is an analytical aggregation, not proof of the identity of every investor.

Broker codes identify intermediaries, not necessarily ultimate beneficial owners.

Never claim to know which named individual supplied the capital unless that information is lawfully public and verifiable.

Only expose investor classes, foreign/domestic categories, institutional categories, or beneficial ownership when the provider actually supports those fields.

Do not label flows as "smart money" without a defensible methodology.

### E. Data providers

Create a source/provider matrix.

For each provider document:
- Data fields.
- Historical coverage.
- Latency.
- Licensing.
- Redistribution rights.
- Update frequency.
- Known limitations.

If data is unavailable, return UNAVAILABLE rather than fake values.

### Acceptance

- Market-wide capital flow API.
- Stock-specific capital flow API.
- Flow history.
- Provenance.
- Data freshness.
- Coverage reporting.
- Regression tests.
- Frontend Capital Flow experience.

---

## TASK 116 — Portfolio Management System

Objective:

Build personal portfolio tracking AND model portfolio tracking.

Both are required.

### A. Personal portfolio

Support manual entry and CSV import initially.

Track:
- Holdings.
- Quantity.
- Average cost.
- Acquisition lots.
- Cash.
- Buy/sell transaction history.
- Fees.
- Taxes when applicable.
- Dividends.
- Corporate actions.
- Realized P&L.
- Unrealized P&L.
- Portfolio value.

No broker integration or live order execution in this task.

### B. Model portfolios

Support:
- Virtual portfolios.
- Investment strategies.
- Benchmark comparisons.
- Rebalancing simulations.
- Historical snapshots.
- Performance comparisons.

### C. Portfolio analytics

Implement:

- Asset allocation.
- Sector allocation.
- Concentration.
- Position contribution.
- Realized returns.
- Unrealized returns.
- Total return.
- Dividend income.
- Cash allocation.
- Benchmark comparison.

### D. Performance measurement

Time-Weighted Return:

TWR = Product of (1 + subperiod return) - 1

Money-Weighted Return:

Calculate using XIRR/IRR with dated cash flows.

Explain the difference between TWR and MWR.

### E. Accounting

Support a documented cost-basis policy.

Handle:
- Fees.
- Partial sales.
- Dividends.
- Stock splits.
- Reverse splits.
- Rights issues.
- Stock bonuses.

Never silently assume missing transaction details.

### Acceptance

- Personal portfolio.
- Model portfolio.
- Transaction ledger.
- Portfolio valuation.
- P&L.
- Performance reports.
- Tests against manually calculated examples.

---

## TASK 117 — Quantitative Portfolio & Risk Lab

Objective:

Build a professional quantitative portfolio analysis and risk engine.

This is one of the most important FinSentiMind features.

### A. Modern Portfolio Theory — Markowitz

Implement mean-variance portfolio optimization.

Portfolio expected return:

E(Rp) = w'μ

Portfolio variance:

σp² = w'Σw

Where:
- w = portfolio weights.
- μ = expected returns.
- Σ = covariance matrix.

Implement:
- Minimum variance portfolio.
- Maximum Sharpe portfolio.
- Target return optimization.
- Efficient Frontier.
- Portfolio volatility.
- Expected return.
- Asset correlation.
- Allocation constraints.

Support constraints such as:
- Long-only.
- Maximum position weight.
- Minimum cash.
- Sector concentration limits.

Show the assumptions behind expected returns and covariance estimates.

### B. CAPM

Expected return:

E(Ri) = Rf + βi × (E(Rm) - Rf)

Show:
- Beta.
- Risk-free rate.
- Market risk premium.
- Historical estimation period.

Do not treat CAPM as guaranteed future return.

### C. Risk Parity

Implement an optional risk-parity allocation approach.

Show asset risk contribution and explain its limitations.

### D. Monte Carlo Simulation

Implement Monte Carlo portfolio simulation.

Prefer historical bootstrap and block bootstrap when appropriate.

Support parametric simulation only with clearly stated distribution assumptions.

For a geometric Brownian motion scenario:

P(t+Δt) = P(t) × exp((μ - 0.5σ²)Δt + σ√Δt Z)

Where Z is a random standard normal variable.

Generate:
- Multiple portfolio paths.
- Expected terminal value.
- Median outcome.
- 5th percentile.
- 25th percentile.
- 75th percentile.
- 95th percentile.
- Probability of loss.
- Probability of reaching target wealth.
- Drawdown distributions.

Use deterministic seeds for reproducibility.

Never present simulated results as predictions.

### E. Value at Risk

Support:
- Historical VaR.
- Parametric VaR.
- Monte Carlo VaR.

Clearly show confidence level and holding period.

### F. Conditional Value at Risk

Expected Shortfall:

CVaR = Expected loss conditional on loss exceeding VaR.

### G. Additional risk metrics

- Maximum drawdown.
- Annualized volatility.
- Sharpe ratio.
- Sortino ratio.
- Calmar ratio.
- Beta.
- Alpha, where a valid benchmark exists.
- Tracking error.
- Information ratio.
- Correlation.
- Downside deviation.
- Rolling volatility.

### H. Stress testing

Support user-defined scenarios:
- Broad market decline.
- Sector shock.
- Currency shock.
- Commodity shock.
- Interest-rate shock.
- Correlation breakdown.

Do not fabricate historical crisis data.

### I. Hedge-fund-style analysis

Add:
- Factor exposure.
- Rolling beta.
- Return attribution.
- Risk contribution.
- Drawdown attribution.
- Benchmark-relative performance.
- Strategy comparison.

Only calculate factor exposures where the required data exists.

### Acceptance

A complete Quant Risk Lab with reproducible calculations, visualizations, formula explanations, and tests.

---

## TASK 118 — Macro & Cross-Asset Intelligence

Objective:

Expand global context for Indonesian equity research.

Monitor, where reliable data is available:

Equity indices:
- S&P 500.
- Dow Jones.
- Nasdaq.
- Hang Seng.
- Other relevant Asian benchmarks.

Currencies:
- USD/IDR.
- Other relevant FX pairs.

Commodities:
- Gold.
- Coal.
- Nickel.
- CPO.
- Oil.

Macro indicators:
- Interest rates.
- Bond yields.
- Inflation.
- Central-bank decisions.
- Other available economic indicators.

### Requirements

- Provider adapters.
- Source provenance.
- Timezone normalization.
- Currency normalization.
- Data freshness.
- Historical observations.
- Unavailable state.
- Cross-asset comparison.

Connect macro indicators to IDX sector research as analytical context.

Do not claim causation based only on correlation.

Do not use speculative commodity symbols as verified market prices.

Respect provider licensing and API terms.

### Acceptance

Macro dashboard with actual sourced data, clear unavailable states, and historical context.

---

## TASK 119 — Integrated Research & Market Monitoring Workstation

Objective:

Integrate all completed research capabilities into a coherent terminal.

The design direction is locked:

Hybrid Bloomberg-style terminal × Modern Quant Research Workstation.

Research and monitoring have EQUAL priority.

### Core navigation

1. Market Overview.
2. Market Monitor.
3. Stock Explorer.
4. Stock Detail.
5. Company Research.
6. Fundamental Valuation.
7. Capital Flow.
8. Watchlist.
9. Portfolio.
10. Model Portfolio.
11. Quant Risk Lab.
12. Macro Context.
13. Data Health.
14. Research Reports.

### Stock Detail

Consolidate:
- Price history.
- Quant features.
- Financial fundamentals.
- Valuation.
- Capital flow.
- News and sentiment.
- Sector context.
- Market regime.
- Research notes.
- Data quality.

### Research workspace

Allow:
- Investment thesis.
- Bull/base/bear scenarios.
- Valuation assumptions.
- Research notes.
- Risk factors.
- Catalysts.
- Source references.
- Historical thesis revisions.

### Educational formula experience

For each important model, provide:
- Formula.
- Variable definitions.
- Worked example.
- Interpretation.
- Assumptions.
- Limitations.
- Data sources.

Create:

docs/FINANCIAL_MODELS_CATALOG.md

The frontend should allow users to understand how the model produces its output.

### UX requirements

- Balanced information density.
- Excellent typography.
- Monospace financial numbers where appropriate.
- Clear hierarchy.
- Fast navigation.
- Responsive layouts.
- Accessible interactions.
- Keyboard command palette.
- No unnecessary decorative elements.
- No fake charts or metrics.
- No unnecessary UI frameworks.

Keep the existing Vanilla JS architecture unless a strong technical reason justifies a change.

---

## TASK 120 — Personal Operations, Reliability & Public Readiness

Objective:

Harden the personal workstation and prepare an honest assessment for future public expansion.

### A. Personal security

- Secret management.
- Environment configuration.
- Safe API errors.
- Local access controls.
- CORS review.
- Input validation.
- File import validation.
- Backup and recovery.
- Audit logging.
- Dependency security review.

### B. Data reliability

- Freshness monitoring.
- Source health.
- Refresh reports.
- Dataset checksums.
- Historical generation retention policy.
- Recovery procedure.
- Failure notifications or clear status.
- Reproducible research artifacts.

### C. Operational readiness

Document:
- Installation.
- Startup.
- Refresh.
- Backup.
- Restore.
- Troubleshooting.
- Known limitations.
- Provider configuration.

### D. Future public-readiness assessment

Assess, but do not automatically launch:

- IDX data licensing.
- Redistribution rights.
- Provider commercial terms.
- Indonesian privacy obligations.
- PSE registration applicability.
- OJK/financial-service regulatory boundaries.
- Corporate structure.
- Multi-user architecture.
- Authentication.
- Authorization.
- Tenant isolation.
- Audit trail.
- Data retention.
- Deployment architecture.
- Monitoring and incident response.

Use current authoritative sources when researching legal and licensing matters.

Clearly distinguish technical requirements from legal conclusions.

Recommend qualified Indonesian legal/compliance review before commercial operation.

### Explicit exclusions

Do not implement:
- Public registration.
- Multi-user production deployment.
- Real broker integration.
- Live order routing.
- Custody of assets.
- Automated trading.

These belong to a future separately approved roadmap.

### Acceptance

A secure and reliable personal workstation with a documented public-readiness gap assessment.

---

## TASK 121 — FINAL PRODUCT ACCEPTANCE

This is the FINAL TASK of this roadmap.

Do not invent additional implementation tasks after TASK 121.

Objective:

Verify that FinSentiMind operates as a complete personal research and portfolio intelligence workstation.

### A. Full regression testing

Run all existing and newly added tests.

Check:
- Data pipeline.
- Dataset generations.
- Trading calendar.
- Time alignment.
- Daily sentiment.
- Instrument master.
- Quant features.
- Market intelligence.
- ML research.
- Fundamental metrics.
- Valuation.
- Capital flow.
- Portfolio.
- Risk engine.
- Macro.
- APIs.
- Frontend.

### B. Numerical validation

Validate financial calculations using independently calculated examples.

Specifically:
- Returns.
- Financial ratios.
- Owner earnings.
- DCF.
- Margin of safety.
- Portfolio returns.
- XIRR.
- Markowitz optimization.
- Sharpe.
- Sortino.
- VaR.
- CVaR.
- Monte Carlo distributions.
- Maximum drawdown.

Test missing data, extreme inputs, invalid assumptions, and numerical stability.

### C. Data validation

Verify:
- Source provenance.
- Data freshness.
- Missingness.
- Duplicate keys.
- Historical consistency.
- Corporate actions.
- Look-ahead prevention.
- Dataset versioning.
- No fabricated values.

### D. API validation

Verify every endpoint, error response, schema, and backward compatibility.

### E. Browser acceptance

Verify every major page in a real browser.

Test:
- Desktop.
- Tablet.
- Mobile.
- Command palette.
- Search.
- Charts.
- Watchlist.
- Portfolio.
- Valuation.
- Capital flow.
- Risk lab.
- Data Health.

Check browser console errors and network failures.

Capture final screenshots.

Destination:

docs/screenshots/final-product-acceptance/

### F. Documentation

Finalize:

- docs/PROJECT_HANDOFF.md
- docs/FINAL_ARCHITECTURE.md
- docs/MVP_RUNBOOK.md
- docs/refresh-workflow.md
- docs/FINANCIAL_MODELS_CATALOG.md
- docs/PRODUCT_VISION.md
- docs/PRODUCT_ROADMAP.md
- docs/PUBLIC_READINESS.md

Document actual behavior only.

### G. Security audit

Verify:
- No secrets.
- No .env.
- No private database dumps.
- No accidental Parquet staging.
- No insecure default credentials.
- No unauthorized external mutations.

### H. Final product report

Create:

docs/FINAL_PRODUCT_ACCEPTANCE.md

Include:

1. Product architecture.
2. Completed tasks.
3. Feature inventory.
4. Data source inventory.
5. Financial models.
6. Test results.
7. Browser results.
8. Data freshness.
9. Known limitations.
10. Unavailable providers.
11. ML validation status.
12. Licensing limitations.
13. Security assessment.
14. Public-readiness assessment.
15. Remaining external approval requirements.

### Final status

The product must be honestly classified as:

- Personal Research Ready.
- Portfolio Monitoring Ready.
- Market Monitoring Ready.
- Fundamental Research Ready, where verified data coverage supports it.
- Quantitative Risk Analysis Ready, where input quality supports it.
- Public Launch: NOT APPROVED until legal, licensing, security, and operational requirements are independently satisfied.
- Live Trading: NOT IMPLEMENTED.

TASK 121 is complete only when all implemented features have passed their relevant acceptance criteria and remaining unavailable external capabilities are clearly disclosed.

---

# PART 5 — GIT AND EXECUTION STRATEGY

## Initial repository

Start by verifying:

- Current branch.
- HEAD.
- Working tree.
- Remote tracking.
- Completed milestone commits.

Never discard existing work.

## Branch strategy

TASK 107A:

feat/task-107a-refresh-edge-cases

After successful TASK 107A completion, create the coordinated continuation branch:

feat/finsentimind-investment-suite

Base it on the accepted TASK 107A branch.

Keep independent commits for each milestone:

- TASK 107B.
- TASK 113.
- TASK 114.
- TASK 115.
- TASK 116.
- TASK 117.
- TASK 118.
- TASK 119.
- TASK 120.
- TASK 121.

Do not rewrite previous milestone commits.

Do not merge into main.

## Commit strategy

After each completed task:

1. Review changed files.
2. Run targeted tests.
3. Run relevant integration tests.
4. Run full tests when appropriate.
5. Run git diff --check.
6. Inspect git status.
7. Verify secrets/artifacts are not staged.
8. Commit with a descriptive message.
9. Push the feature branch.
10. Record the commit hash.

If tests fail, fix the cause before marking the task complete.

Never weaken tests to obtain green results.

## Autonomous execution

Continue through the roadmap without asking the owner to reconfirm decisions already locked in this prompt.

If a provider is unavailable:
- Complete the local architecture.
- Implement the adapter contract.
- Test with fixtures.
- Document unavailable functionality.
- Continue with independent tasks.

If a task requires external owner approval:
- Prepare the safe implementation.
- Complete local testing.
- Do not execute the external mutation.
- Record the approval gate.
- Continue other independent work.

Never claim a gated task is fully accepted.

---

# PART 6 — FINAL PRODUCT PHILOSOPHY

FinSentiMind must be built around five principles:

1. Evidence over assumptions.
2. Explainability over black-box outputs.
3. Data integrity over feature quantity.
4. Risk awareness over unrealistic return promises.
5. Research quality over superficial visual complexity.

A model that performs poorly must be reported honestly.

A provider that is unavailable must remain unavailable.

A financial value that cannot be verified must not be invented.

A capital-flow metric must not pretend to identify anonymous individual investors.

A valuation must expose its assumptions.

A Monte Carlo simulation must expose its distribution assumptions.

A portfolio optimizer must explain its inputs and limitations.

A portfolio dashboard must distinguish actual holdings from model portfolios.

The application is an analytical and educational research tool, not a guarantee of investment returns.

---

# FINAL INSTRUCTION

Start with TASK 107A.

Proceed sequentially through TASK 121.

Preserve the existing working architecture.

Do not rebuild completed milestones.

Do not skip tests.

Do not fabricate data.

Do not perform unauthorized external mutations.

Commit and push completed milestones to feature branches.

Never merge into main without explicit owner authorization.

The ultimate deliverable is a complete, reliable, explainable, and auditable personal investment research and portfolio monitoring workstation.

**TASK 121 IS THE FINAL ACCEPTANCE MILESTONE OF THIS ROADMAP.**
