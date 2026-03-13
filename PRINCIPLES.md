# CaptionPilot — Building Principles

> These are the laws we don't break. Every feature, fix, and decision runs through these filters.

---

## 1. Multi-Tenancy First
Every line of code is written for N customers, never one. If a script references a specific account name, username, path, or credential inline — it's wrong. Config drives everything. Adding a new customer should require only a config file, never a code change.

## 2. Ease of Use Over Everything
If a feature requires a customer to log into a new platform, download a new app, or learn a new interface — rethink it. The approval flow lives in iMessage because that's where people already are. The upload flow lives in Apple Photos because that's where people already are. We meet customers where they live.

## 3. No New Logins
Customers have enough passwords. CaptionPilot has zero user-facing login screens. Authentication happens through the platforms they already use (iPhone, Instagram). If a feature needs auth, solve it invisibly.

## 4. Config Over Code
No hardcoded values in scripts. Ever. Account names, handles, post times, thresholds, feature flags — all in `account_configs/{account}.json`. This makes every account independently configurable and makes the system auditable.

## 5. Secrets in Files, Never Code
Credentials, tokens, API keys — never in source code, never in Discord, never in logs. Files with restricted permissions (`chmod 600`), gitignored, outside the repo where possible. If a secret has to change, only the config file changes.

## 6. Graceful Degradation
If AI is unavailable, fall back to templates. If Instagram blocks a post, alert and retry — never silently fail. If a script crashes, the customer's content should never be lost. Every failure mode has a defined recovery path.

## 7. Observability by Default
Every meaningful action is logged with timestamp and account. The admin console shows all state. If something goes wrong at 3 AM, we should be able to reconstruct exactly what happened from logs alone. No mystery failures.

## 8. Account Isolation is Sacred
No shared state between customers. No shared directories. No shared sessions. Cross-account contamination is the worst possible failure — content posted to the wrong account destroys trust instantly and permanently. Isolation guards are non-negotiable.

## 9. Cost Consciousness
- Use Haiku for speed/volume tasks (health checks, classification, simple decisions)
- Use Sonnet for quality tasks (caption generation, visual analysis)
- Measure token usage per account per month — cost must be predictable at scale
- Never make AI calls in loops without rate limiting

## 10. Stability > Features
A feature that breaks existing behavior is worth negative value. QA runs after every commit. No feature ships without passing the full test suite. When in doubt, ship nothing and ask.

---

## Team Roles

| Role | Owner | Channel | Cadence |
|------|-------|---------|---------|
| CTO/CPO | Jairy | #cp-strategy | Daily brief, weekly roadmap |
| UX/Product | Jairy (UX persona) | #cp-product | Per feature + weekly review |
| DevOps/SRE | Jairy (Eng persona) | #cp-engineering | Per deploy + weekly infra review |
| R&D | Jairy (R&D persona) | #cp-rd | Weekly experiments brief |
| QA | Jairy (QA persona) | #cp-qa | Per commit + weekly coverage report |

---

## What CaptionPilot Actually Is

**Not:** "An AI posting tool."

**Yes:** A personal social media team for small business owners who can't afford a social media manager. The value prop is: *I never have to think about Instagram again.*

The customer takes a photo of their work. CaptionPilot writes the caption, picks the best photos, posts at the right time, gets the hashtag strategy right — and all they did was tap "Approve" from their phone. That's the product.

---

## Graduation Criteria (when to grow beyond OpenClaw)

| Milestone | Action |
|-----------|--------|
| 5 paying customers | Move API server to Railway or Fly.io |
| 10 customers | Proper secrets management (Doppler or AWS Secrets Manager) |
| 25 customers | Database replaces JSON state files (Postgres) |
| 50 customers | Dedicated photo processing worker (separate from API) |
| 100 customers | Multi-region, proper SLAs, on-call rotation |

---

*Last updated: 2026-03-13*
