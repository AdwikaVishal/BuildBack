# Submission Checklist

Status legend: **VERIFIED** = confirmed live in this audit · **MISSING** =
not yet done · **TEAM CONFIRMATION** = needs a team decision/input.
License is listed for completeness of the Devpost checklist only; per the
current phase's boundaries, no action or recommendation is made here.

| # | Item | Status | Notes / next action |
|---|---|---|---|
| 1 | Project name + tagline | TEAM CONFIRMATION | Draft in `devpost-submission.md` ("RippleBack" + tagline) — approve or adjust |
| 2 | One-line pitch (< field limit) | TEAM CONFIRMATION | Use tagline; trim to Devpost's limit when copying into the form |
| 3 | Public repository URL | MISSING / TEAM CONFIRMATION | Local repo only; audit made no commits/pushes. Team must publish `New_endpoints` (or merged branch) publicly before submission |
| 4 | Repository content sanity | VERIFIED (local) | README setup instructions, demo runbook, tests, docs present; no secrets in tracked files (`.env` gitignored and absent) |
| 5 | LICENSE file | TEAM CONFIRMATION | No LICENSE file exists in the repo; decision deliberately deferred to the team this phase |
| 6 | Written Devpost description | VERIFIED (draft) | `devpost-submission.md` drafted — needs team review, then paste into form |
| 7 | Demo video (< 3 min) | MISSING (script VERIFIED) | `demo-script.md` ready; recording not yet made. Only verified beats included |
| 8 | Demo video hosting link | MISSING | Record first; host per Devpost rules (YouTube/Drive unlisted etc.) |
| 9 | Screenshots / images | MISSING (list VERIFIED) | Capture the 10 in `screenshots-list.md` from the live stack |
| 10 | Live app behavior backing the video | VERIFIED | 659/659 tests; 17 live MCP tools; 5-call recovery workflow; write refusals; deterministic fallback with explicit reason; plan unchanged post-demo |
| 11 | Bedrock status honesty | VERIFIED | Provider implemented + fallback verified; **live inference NOT verified** (no AWS creds/region/model access). Keep caveat in submission |
| 12 | Optional: live Bedrock screenshot | TEAM CONFIRMATION | Only if real inference succeeds before the deadline (see screenshots-list #11) |
| 13 | Product-feedback answers | VERIFIED (draft) | `product-feedback.md` — fill **[TEAM INPUT NEEDED]** markers before submitting |
| 14 | Track selection: Alexa+ (primary) | TEAM CONFIRMATION | Track relevance drafted (Bedrock intent parsing = Alexa-shaped pattern; skill itself is future work — do not claim it exists) |
| 15 | Track selection: AWS Builder mini-challenge | TEAM CONFIRMATION | Bedrock Converse integration is the qualifying piece; keep "live inference pending" caveat |
| 16 | Team member names/roles | TEAM CONFIRMATION | Not in repo metadata; add in form |
| 17 | Submission form filled + submitted | MISSING | Depends on 3, 5, 6, 7, 8, 9, 14–16 |
| 18 | Post-demo integrity check | VERIFIED (last run) | Plan version 1 / healthy / 0 disruptions after all demo probes; writes remain disabled |

## Critical-path order before the deadline

1. Team: publish repo (3), decide license (5), approve name/tracks (1, 14, 15)
2. Record video from `demo-script.md` (7) → get link (8)
3. Capture screenshots (9)
4. Fill **[TEAM INPUT NEEDED]** in `product-feedback.md` (13)
5. Paste description into the form, attach everything, submit (6, 17)
