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

## Vor dem Pflichtstellen eines Repos

Bevor ein Repo in die Include-Liste kommt, wird der Checker lesend gegen seine
letzten gemergten PRs abgespielt. Das Skript `scripts/replay_merged_prs.py`
holt sie über die GitHub-API und fährt `check_receipt.main` je PR mit echten
Daten (Kopf- und Basis-SHA des PR, Autor als Ereignis-Absender). Es schickt
nur GET-Anfragen; das Token kommt aus `GITHUB_TOKEN` oder `gh auth token` und
wird nie ausgegeben.

```sh
python3 -m scripts.replay_merged_prs exigo-AI-Studio/exigo-platform 25
```

Eine Zeile je PR (Nummer, Autor, `ok` oder `ROT`, Meldung des Checkers), am
Ende die Summe. Exit 0 heißt alle grün, 1 mindestens ein roter PR, 2 kein
Token, die PR-Liste war nicht lesbar oder es gab keinen gemergten PR. Ohne Zahl gelten 25 PRs.

Lesart: ein Repo ist bereit, wenn alle grün sind. Jeder rote PR wird einzeln
angesehen, denn er hätte beim Pflichtstellen einen Merge blockiert. Die
üblichen Ursachen:

- **Fehlender Status.** Meldung `receipt missing`: am Kopf des PR steht kein
  `gate-receipt`. Das Repo veröffentlicht die Quittung noch nicht als
  Commit-Status (so bei factory-probe am 2026-10-22, Zahlen siehe unten). Das ist
  eine echte Lücke im Repo, nicht im Skript; erst schließen, dann aufnehmen.
- **Bot-Absender.** Ein Bot-PR ist rot, obwohl er nur Abhängigkeiten ändert.
  Das Ereignis trägt den Login als `renovate[bot]`, `gh pr list --json
  author` liefert `app/renovate`. Die REST-Liste, die das Skript liest,
  trägt schon `renovate[bot]`; `sender_for` rechnet die gh-Schreibweise
  trotzdem um, damit eine mit gh gebildete PR-Liste nicht falsch rot wird. Bleibt
  ein Bot-PR trotzdem rot, prüfen, ob jeder Commit vom Bot stammt, von GitHub
  signiert ist (`web-flow`, verifiziert) und der PR vom Bot geöffnet wurde.
- **Datei außerhalb der Bot-Liste.** Ein Bot-PR berührt neben Abhängigkeiten
  etwas anderes (zum Beispiel Quellcode oder eine Compose-Datei, die nicht
  in `DEPENDENCY_FILE_PATTERNS` steht). Die Bot-Regel greift dann nicht, die
  Quittung wird verlangt. Entweder ist der PR zu Recht rot, oder die Liste
  braucht ein Muster (eigener PR, gemessen an der konkreten Datei).

Grenzen: die PR-Liste ist nach letzter Änderung sortiert, nicht nach
Merge-Zeitpunkt; ein sehr aktives Repo kann einen kürzlich gemergten PR
auslassen. Der Status wird zum heutigen Stand gelesen, nicht zum Merge-Zeitpunkt.

## Reihenfolge beim Anwenden

1. `exigo-AI-Studio/gate-policy` in claude-config `tools/session-bot-repos.txt`
   aufnehmen und die App `exigo-session` hier installieren: mit
   Code-Owner-Review ohne Bypass kann MvR nur PRs freigeben, die der Bot
   geöffnet hat, nie seine eigenen.
2. Dann `repo-gate-policy-main.json` und `repo-gate-policy-tags.json` auf
   dieses Repo anwenden (`--repo exigo-AI-Studio/gate-policy`).
3. Die Org-Rulesets anwenden (`admin:org`). Ein Repo kommt in die
   Include-Liste, sobald der Checker gegen echte PRs dieses Repos bestanden hat
   (Tabelle der Fälle: claude-config, Plan 2, Task 6, Step 3; seit dem
   2026-10-21 statt „zehn grüne Läufe je Repo"). Erster Zug: sieben Repos,
   angewendet am 2026-10-21 (Ruleset 25558192). Zweiter Zug: factory-selfhost,
   factory-runtime und exigo-platform. Vorher wurde der Checker lesend gegen
   ihre letzten 20 bis 25 gemergten PRs abgespielt: 65 von 65 grün, darunter
   zwei Renovate-PRs in exigo-platform über die Bot-Regel; den Dependabot-Fall
   zeigt ein abgespielter PR aus gusto-helvetico (#321, erster Zug). Noch nicht
   dabei ist factory-probe: bei 15 von 25 abgespielten PRs fehlte der
   Quittungs-Status, weil das Register es noch nicht als `receipt-check` führt.
   Die Aufnahme ersetzt die alten Prüfungen nicht: `gate-receipt` in
   factory-selfhost und factory-runtime (Repo-Ruleset) und der PR-Schritt im
   `gate`-Job von exigo-platform bleiben, bis sie je Repo in einem eigenen
   Schritt entfernt werden, nach dem Anwenden und mit eigenem Review; bis
   dahin gelten beide Prüfungen nebeneinander.
