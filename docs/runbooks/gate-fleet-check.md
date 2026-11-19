# Runbook: gate-fleet-check

## Was er tut

Täglicher Abgleich, welche Repos im Register des Verification-Gates das Gate
wirklich tragen und welche von dem abweichen, was das Register behauptet. Er
ruft `~/.claude/tools/gate doctor --fleet` auf (das Werkzeug liegt in
`claude-config`) und meldet über den vendorten Client `hc-run` an
exigo-watchtower, Check `gate-fleet-check`. Er repariert nichts.

Bis November 2026 lief er als `bin/gate-fleet-check` in `mvroeder/obs` und
meldete an pulse (gate-policy#11, Abbau von pulse in mvroeder/obs#160).

## Takt

- launchd: `com.mvroeder.gate-fleet-check`, täglich 06:30, nur Mac mini
- Healthchecks: `cron` `30 6 * * *`, Europe/Berlin, Grace 4h
- Plist: `launchd/com.mvroeder.gate-fleet-check.plist`, Wrapper
  `scripts/gate-fleet-check.sh`, Client `vendor/hc-signal/hc-run`

## Urteil

| Exit | Zustand | Wann |
|------|---------|------|
| 0 | `ok` | Flotte sauber, oder nur Arbeitslisten-Funde (`flip-condition-met`, `template-behind`) |
| 1 | `fail` | ein Defekt (`push-unguarded`, `install-broken`, `unregistered`, `extra-checkout`) oder das Register ist unlesbar |
| 75 | `unchecked` | `gate` fehlt oder lief nicht (126, 127, Absturz); Alarm erst ab dem dritten Lauf in Folge |

Die erste Zeile nach `state=...` im Ping ist die Zusammenfassung. Sie nennt
höchstens fünf Defekte und sagt ausdrücklich, wie viele sie weglässt.

## Wo zuerst nachsehen

- Log mit allen `FIND`-Zeilen: `~/.claude/logs/gate-fleet-check.log`
- Fehler des Starts: `~/.claude/logs/gate-fleet-check-error.log`
- Ereignisprotokoll des Checks in exigo-watchtower

## Typische Fälle

- **`push-unguarded`:** die maschinenweiten Gate-Hooks sind nicht registriert,
  ein ungeprüfter `git push` geht in jedem Gate-Checkout durch. Eine Zeile mit
  Remote "(machine)", immer zuerst genannt. Abhilfe: Hooks nach
  `claude-config` `docs/verification-gate.md` registrieren.
- **`install-broken`, `unregistered`, `extra-checkout`:** ein Repo, das das
  Register als abgedeckt führt, ist es nicht. Volle `FIND`-Zeilen im Log
  lesen, je Repo beheben oder den Register-Eintrag nachziehen, wenn die
  Abweichung gewollt ist.
- **`ok` mit "N repo(s) flip-condition-met, none paged":** Arbeitsliste, kein
  Defekt. Ein zurückgestelltes Repo hat seine eigene Kippbedingung erreicht und
  braucht eine Entscheidung. Täglicher Alarm darauf erzöge zum Wegsehen.
- **`template-behind`:** kommt seit claude-config#361 nicht mehr vor, bleibt
  für alte Logzeilen.
- **`register unreadable`:** `gate-fleet.json` fehlt oder ist kein gültiges
  JSON. Damit ist jede andere Aussage des Checks wertlos, deshalb `fail` und
  nicht `unchecked`. `~/.claude/gate-fleet.json` prüfen.
- **`gate binary missing at ...` oder `gate exited N`:** das Werkzeug selbst
  fehlt oder stürzt ab. Den `claude-config`-Checkout prüfen, der es
  installiert.
- **Check `down` ohne `state=fail`:** der Job lief nicht. `launchctl print
  gui/$(id -u)/com.mvroeder.gate-fleet-check` und das Error-Log ansehen; fehlt
  der Key unter `HC_PING_KEY_FILE`, meldet `hc-run` das auf stderr.

## Befehle

```bash
# Handlauf ohne Ping (zeigt Log und Zusammenfassung, Exit-Code ist das Urteil):
bash ~/dev/exigo-ai-studio/gate-policy/scripts/gate-fleet-check.sh; echo "exit $?"

# Unbeaufsichtigter Lauf über launchd (der einzige gültige Beleg für den Ping):
launchctl kickstart -k gui/$(id -u)/com.mvroeder.gate-fleet-check

# Installation oder Neuinstallation aus dem Haupt-Checkout auf main:
cp ~/dev/exigo-ai-studio/gate-policy/launchd/com.mvroeder.gate-fleet-check.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.mvroeder.gate-fleet-check 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mvroeder.gate-fleet-check.plist
```

## Drill

`HC_SIGNAL_DRILL=fail` lässt sich über launchd nicht an genau einen Job geben
(Inventar: `drill = { via = "none" }`, wie bei den anderen launchd-Jobs am
Mini). Von Hand, mit Key und Zustandsverzeichnis wie in der Plist:

```bash
HC_SIGNAL_DRILL=fail HC_PING_KEY_FILE=~/.config/exigo/hc-ping-key \
  HC_STATE_DIR=~/.local/state/hc-signal \
  bash ~/dev/exigo-ai-studio/gate-policy/vendor/hc-signal/hc-run gate-fleet-check -- \
  /bin/bash ~/dev/exigo-ai-studio/gate-policy/scripts/gate-fleet-check.sh
```

Erwartet: `start` und `fail` mit derselben `rid`, Alarm mit
`state=fail reason=drill`. Danach ein normaler Lauf per `kickstart`.

## Eskalation

- Liegenlassen, wenn: die Funde bekannt sind, etwa `flip-condition-met` für ein
  Repo, das laut eigener Kippbedingung später ans Gate kommt.
- Sofort beheben, wenn: `push-unguarded` irgendwo, `install-broken` an einem
  Repo, das das Register als `gated` führt, oder `register unreadable`.
