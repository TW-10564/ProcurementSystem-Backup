# docs/

Everything that is documentation rather than code. The repo root now holds only
what has to live there: `CLAUDE.md`, `Dockerfile`, `docker-compose.yml`,
`.dockerignore`, `.gitignore`, `.gitattributes`.

| Path | What |
|---|---|
| `SSO_TO_GOLIVE_TASKS.md` | The live plan: SSO → DGX setup → deployment checks → handover |
| `SECURITY_AUDIT_2026-07-30.md` | Pre-deployment security audit; findings F1–F8 and what was fixed |
| `Phase1_Project_Charter.md` | Formal charter (v1.1, matches the finalized Phase-1 scope) |
| `Phase1_Requirements_Suite.md` | Requirements companion to the charter |
| `business/` | Deliverables for management: 稟議書, 要件定義書, プロジェクト憲章書, the filled requirements workbook, and `Data Summary.xlsx` (referenced from `forecasting.py`'s accuracy notes) |
| `presentation/` | Report drafts, the presentation script, and the Phase-1→Phase-2 migration diagrams |
| `assets/` | Logos and other shared images |
| `archive/` | Finished or superseded working documents — kept for the reasoning they record, not as instructions |

Operational runbooks live with the thing they operate, in `deploy/`:

| Path | What |
|---|---|
| `deploy/README-docker.md` | Docker/Postgres deployment on the DGX |
| `deploy/README-cd.md` | Self-hosted runner + continuous deployment |
| `deploy/README.md` | The older systemd/SQLite deployment — still the rollback path |

`app/IMPLEMENTATION_PLAN.md` stays next to the code it describes.
