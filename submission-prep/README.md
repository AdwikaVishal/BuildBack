# RippleBack — Hackathon Submission Prep (drafts only)

Folder for the Devpost submission drafts for the *Build, Ship, Shape: Amazon
Developer Hackathon* (Alexa+ primary track; AWS Builder mini-challenge).

**Status: DRAFTS — nothing here is submitted, and no existing project file was
edited to create these.** All claims in these drafts are drawn from behavior
verified during the Stage 0–2 audit sessions on branch `New_endpoints`
(commit `51c0cc8`). Anything not verified is marked as such.

## Files

| File | Purpose |
|---|---|
| `devpost-submission.md` | Full Devpost write-up draft (name, tagline, problem, solution, architecture, Bedrock status, challenges, track relevance) |
| `demo-script.md` | Under-three-minute demo video script using only verified behavior |
| `screenshots-list.md` | Exact screenshots to capture from the live app and what each proves |
| `product-feedback.md` | Product-feedback answers for the tools/APIs/SDKs used, with team-input markers |
| `submission-checklist.md` | Submission checklist: verified / missing / needs team confirmation |

## Verified vs. assumed

**Verified (live, this audit):** full test suite 659/659; MCP server on
`http://127.0.0.1:8000/mcp` (Streamable HTTP); adapter on `:8001`; 17
live-discovered MCP tools via `/api/mcp/tools`; the payment-API recovery
workflow executed 5 real MCP tool calls with real intermediate results;
write tools refused while `RIPPLEBACK_WRITES_ENABLED` is unset; deterministic
assistant fallback with an explicit Bedrock-unavailable reason; frontend dev
server proxying `/api` to the adapter; Bedrock provider code present and
boto3 installed (inference itself NOT verified — no AWS credentials, region,
or model access on the audit machine).

**Assumed / unverified:** any Devpost page specifics beyond what the team
stated (tracks, rough requirements); Alexa skill surface (does not exist yet
— see draft); cloud deployment, PM integrations, offline-mode behavior at
runtime, and Docker images (code/docs exist; not exercised in the audit).

**Deliberately out of scope this phase:** LICENSE decision, source/config
changes, enabling writes, AWS resources, commits, pushes.
