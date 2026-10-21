# Abhängigkeitsdateien und Bot-Identitäten

Gemessen am 2026-10-21 (Plan 2, Task 1). Die Bot-Regel des Prüfskripts greift
nur, wenn jeder Commit eines PRs von einer dieser Identitäten stammt und alle
geänderten Dateien auf eines dieser Muster passen.

## Bot-Identitäten (`author.login` der Commits; der Committer ist `web-flow`)

| Login | Beleg |
|---|---|
| `dependabot[bot]` | factory-runtime#85, `gh api repos/exigo-AI-Studio/factory-runtime/pulls/85/commits` |
| `renovate[bot]` | exigo-platform#1024, #1025, dieselbe Abfrage |

## Muster je Ökosystem (Regex, auf den vollen Pfad)

| Ökosystem | Muster | Belegt in |
|---|---|---|
| Go | `(^|/)go\.(mod|sum)$` | factory-runtime, factory-selfhost |
| Python (uv) | `(^|/)(pyproject\.toml|uv\.lock|requirements[^/]*\.txt)$` | gusto-helvetico, studio-ops |
| Node | `(^|/)(package\.json|package-lock\.json|pnpm-lock\.yaml)$` | gusto-helvetico, donework.ai |
| Docker | `(^|/)Dockerfile[^/]*$` | exigo-platform (Wurzel), gusto-helvetico (`infra/`) |
| GitHub Actions | `^\.github/workflows/[^/]+\.ya?ml$` | alle Org-Repos |
| Renovate-Konfiguration | `^renovate\.json$` | exigo-platform |

Nur die Wurzel jedes Repos wurde über die Contents-API gelistet; die Muster
sind deshalb pfadunabhängig formuliert, damit `infra/Dockerfile` ebenso zählt
wie `Dockerfile`. Eine Datei außerhalb dieser Muster in einem Bot-PR macht
die Quittung wieder zur Pflicht.
