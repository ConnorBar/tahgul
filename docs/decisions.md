# Decisions

Answers to the open questions in [SPEC.md](SPEC.md), newest first.

| Date | Question | Decision |
| --- | --- | --- |
| 2026-10-20 | App name | **Tahgul.** The spec still says "Ledger (working name)"; the code, env vars (`TAHGUL_*`), token prefix (`thg_`) and UI use Tahgul. |
| 2026-10-20 | Currency | **USD** as the default for new workspaces; each workspace can change it. |
| 2026-10-20 | Tax / VAT | **Off by default.** A workspace or client can set a tax rate later; blank means no tax line. Revisit before invoicing (milestone 7). Owner is in Illinois, where services are generally not subject to state sales tax; confirm with the CPA. |
| 2026-10-20 | Time zone | **America/Chicago** as the default for new workspaces (owner is in Illinois). |
| 2026-10-20 | Invoice number format | **Pick per workspace from presets**, default `{Y}-{seq:3}` (2026-001, resets yearly). Others: `INV-{seq:4}` (continuous), `{Y}{M}-{seq:3}` (resets monthly), `{CLIENT}-{Y}-{seq:3}` (per client, per year). Custom patterns allowed. |
