# Public Readiness Assessment

Status: **PUBLIC LAUNCH NOT APPROVED**

FinSentiMind is currently a personal, local, read-only research workstation.
Technical completeness does not establish legal permission to redistribute data
or operate a public financial service.

## Technical gaps

| Area | Current state | Required before public use |
|---|---|---|
| Authentication | None | Identity, sessions, MFA where appropriate |
| Authorization | None | Per-user access control |
| Tenant isolation | None | Separate data and audit boundaries |
| Public CORS | Disabled | Explicit allowlist only |
| Deployment | Local uvicorn | Hardened hosting, TLS, proxy, monitoring |
| Secrets | Local env | Managed secret storage and rotation |
| Audit trail | Optional local JSONL | Durable, tamper-resistant audit |
| Retention | Local files | Documented retention/deletion policy |
| Backup | Manual local archive | Scheduled, tested recovery |
| Rate limiting | None | Edge/application limits |
| Dependency security | `pip check` only | CI SCA/CVE scanning |
| Incident response | None | Named process and contacts |

## Data licensing and redistribution

Must be reviewed before any public/commercial use:

- IDX market-data licensing and redistribution rights.
- Price/news/sentiment provider terms.
- Macro and cross-asset provider licensing.
- Fundamental document redistribution.
- Capital-flow source permissions.
- Screenshot/report redistribution.
- Attribution requirements.

Unavailable providers must remain unavailable. Do not substitute unlicensed
proxies.

## Indonesian regulatory considerations

This is an engineering checklist, **not a legal conclusion**:

- Personal data protection obligations.
- Electronic system operator (PSE) registration applicability.
- OJK/financial-service regulatory boundaries.
- Investment-advice/advertising restrictions.
- Consumer protection and disclosure requirements.
- Corporate structure and contractual terms.

Qualified Indonesian legal/compliance counsel should review before commercial
operation or public marketing.

## Explicit exclusions

Not implemented and not approved:

- Public registration.
- Multi-user production deployment.
- Broker integration.
- Live order routing.
- Asset custody.
- Automated trading.
- Personalized investment advice at scale.

## Honest classification

| Capability | Status |
|---|---|
| Personal Research | READY (subject to verified imports) |
| Portfolio Monitoring | READY (manual ledger) |
| Market Monitoring | READY (local snapshot) |
| Fundamental Research | READY WHERE VERIFIED DATA EXISTS |
| Quantitative Risk Analysis | READY WHERE INPUT QUALITY SUPPORTS IT |
| Public Launch | NOT APPROVED |
| Live Trading | NOT IMPLEMENTED |
| ML Trading Signal | NOT VALIDATED |
