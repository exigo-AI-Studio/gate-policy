# gate-policy

Org-wide gate policy for exigo-AI-Studio: the reusable workflow `receipt-check`
that verifies a pull request carries a fresh, clean gate receipt, the script
behind it, and the org rulesets as reviewed JSON.

Design: claude-config `docs/superpowers/specs/2026-09-27-gate-vereinheitlichung-design.md`
(part B). Plan: claude-config `docs/superpowers/plans/2026-10-21-gate-vereinheitlichung-plan-2-server-check.md`.

The repository is public since 2026-10-21 (MvR): a caller's workflow runs with
a token scoped to the caller, which cannot check out a private gate-policy.
Nothing here is secret; tag and branch rulesets guard what runs.

Every org repository calls the workflow through an identical file
`.github/workflows/gate-policy.yml` pinned to the moving tag `v1`. The
workflow never checks out or runs caller code; it reads through the API only.

Context name as GitHub reports it (measured 2026-10-21 on gate-policy#1, head
`1479d9a3`): `receipt-check / receipt-check`, that is caller job id, slash,
called job id. The caller file's `name:` does not appear in it. Rulesets,
the wrapup skill and the publish watchdog use this exact string.

## Grenzen, bewusst

- **Admin-Bypass im PR-Modus.** Beide Org-Rulesets tragen den Notausgang der
  Spec: ein Org-Admin kann einen einzelnen PR an `receipt-check` und
  `factory-reviewer` vorbei mergen (`gh pr merge --admin` oder der
  Merge-Endpunkt der API). Direktes Schreiben auf `main` bleibt gesperrt. Das
  Gate sagt also nur für Nicht-Admins immer nein; für MvR ist der Merge-Klick
  die Freigabe, und die globale `CLAUDE.md` stellt genau diesen Weg unter
  Rückfrage. Kein anderes Repo hat eine zweite, bypass-freie Schicht; nur
  gate-policy selbst (`repo-gate-policy-main.json`, `repo-gate-policy-tags.json`)
  kennt keinen Bypass, weil es der Vertrauensanker aller anderen ist.
- **Wer den Status gesetzt hat, prüft das Skript nicht.** Jeder mit
  Schreibrecht kann `gate-receipt` setzen (Spec: „Die Grenze ist benannt,
  nicht versteckt"). Teil C ersetzt den Status durch den Check-Run der
  Reporter-App.
- **Rulesets schützen nicht vor Admins.** Ein Repo-Admin kann die Rulesets
  dieses Repos ändern oder ausschalten; der Audit-Log hält es fest, mehr
  nicht.
- **Bot-Ausnahme.** Im Fenster zwischen dem Branch-Update eines Bots und der
  PR-Eröffnung könnte ein Schreibberechtigter einen als Bot signierten
  API-Commit unterschieben; Dependabot und Renovate rühren Branches mit
  fremden Commits nicht an.

## Reihenfolge beim Anwenden

1. `exigo-AI-Studio/gate-policy` in claude-config `tools/session-bot-repos.txt`
   aufnehmen und die App `exigo-session` hier installieren: mit
   Code-Owner-Review ohne Bypass kann MvR nur PRs freigeben, die der Bot
   geöffnet hat, nie seine eigenen.
2. Dann `repo-gate-policy-main.json` und `repo-gate-policy-tags.json` auf
   dieses Repo anwenden (`--repo exigo-AI-Studio/gate-policy`).
3. Die Org-Rulesets anwenden (`admin:org`). Ein Repo kommt in die
   Include-Liste, sobald die fünf Schatten-Fälle je einmal irgendwo in der Org
   belegt sind (Tabelle in claude-config, Plan 2, Task 6, Step 3; seit dem
   2026-10-21 statt „zehn grüne Läufe je Repo"). Erster Zug: die sieben Repos,
   die `org-gate-transition.json` heute nennt; die vier mit Altvariante
   (exigo-platform, factory-probe, factory-runtime, factory-selfhost) folgen,
   sobald ihre Aufruf-Datei auf `main` liegt, und verlieren im selben Zug den
   alten Pflicht-Check.
