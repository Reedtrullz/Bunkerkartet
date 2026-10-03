# Bunkerkartet

Bunkerkartet er et kildebasert, administrasjonsbeskyttet kart over bunkere,
huler og tunneler, krigsfangeleirer, krigsgraver, krigsminnesmerker og andre
spor etter krigshistorien i og rundt Trondheim.

Kartet er tilgjengelig på <https://bunker.reidar.tech/>. Katalogen er ment som
et arbeidsverktøy for kildeinnsamling, vurdering og planlegging av forsiktige
feltbesøk. En markør er et forskningsspor, ikke en bekreftet inngang eller en
tillatelse til å gå inn på eiendom eller i en konstruksjon.

Appen lager ikke sin egen skraper og kaller ikke en LLM. I stedet kan løse
funn fra forum, aviser, kart og andre kilder behandles av et eksternt
LLM- eller researchoppsett og lastes inn som et strengt JSON-format.

## Funksjoner i core v1

- Kart med egne markører for bunker, hule/tunnel, krigsfangeleir, krigsgrav,
  krigsminnesmerke og andre krigshistoriske steder.
- Søk og filtrering på navn, stedshenvisning, kildetekst, status, type,
  tilgang og usikkerhetsradius.
- Kildeliste og korte begrunnelser på hvert sted.
- Arbeidsflyt for kandidatvurdering: `candidate` -> `researched` ->
  `field-verified` -> `trusted`, eller avvisning.
- Import med forhåndsvisning, validering og idempotent `batch_id`. Felt som
  allerede er kvalitetssikret skal ikke overskrives av en senere kandidatimport.
- Feltobservasjoner med dato, utfall, koordinat, tilgangsnotater og lenker til
  bilder. En funnet observasjon kan godkjennes som ny stedskorrigering og blir
  logget som en revisjonshendelse.
- Feltliste som bare viser steder som uttrykkelig er merket som offentlig
  tilgjengelige for tilnærming.
- Gå-rute via OpenRouteService, lagring av ruter, GPX-nedlasting og GeoJSON-
  eksport. Nettleserens posisjon kan brukes opt-in som startpunkt for en rute;
  posisjonen lagres ikke som en egen observasjon av denne funksjonen, og
  nettleserens målenøyaktighet brukes ikke som stedets usikkerhetsradius.

## Kjør lokalt

Bruk en vedlikeholdt kildekasse og Python 3.12, samme runtime som CI og
containeren. Hvis du starter fra en eldre eller skitten arbeidskopi, inspiser
branch, commit og lokale endringer først; bruk en separat worktree for
vedlikeholdt kilde og behold eksisterende data/WIP. Ikke bruk `reset` eller
`clean` for å gjøre en gammel kopi klar. Kommandoene under bruker en tom,
midlertidig datamappe og bare lokal autentisering.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Start den lokale testen i ett terminalvindu og la prosessen kjøre mens du
prøver importen fra et annet. Avslutt med Ctrl-C; den midlertidige databasen
fjernes når serverprosessen avsluttes.

```sh
set -eu
DATA_DIR=$(mktemp -d)
trap 'rm -rf "$DATA_DIR"' EXIT INT TERM
ADMIN_TOKEN=synthetic-local-only \
BUNKERKARTET_DATA_DIR="$DATA_DIR" \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Åpne <http://127.0.0.1:8765/> og bruk tokenet `synthetic-local-only`.
SQLite-databasen ligger i midlertidig `$DATA_DIR` og deles ikke med produksjon.

## Importer research

Den anbefalte flyten er:

1. Gi kildelenken og relevant tekst til et eksternt LLM- eller researchoppsett
   sammen med [`research/llm-import-prompt.md`](research/llm-import-prompt.md).
2. Lagre JSON-svaret lokalt.
3. Valider pakken før den åpnes i appen:

   ```sh
   .venv/bin/python research/validate_import.py path/to/package.json
   ```

4. Velg filen i appen, forhåndsvis pakken, les gjennom kilder og varsler, og
   trykk `Commit`.

Importerte poster starter som kandidater. Bruk samme `batch_id` på nytt når en
identisk pakke sendes inn; importen er idempotent. Den komplette kontrakten
ligger i [`app/imports.py`](app/imports.py), og arbeidsflyten er beskrevet i
[`research/README.md`](research/README.md).

Den samme forhåndsvisningen og innlastingen kan gjøres mot API-et:

```sh
set -eu
export BUNKERKARTET_URL="${BUNKERKARTET_URL:-http://127.0.0.1:8765}"
export ADMIN_TOKEN='synthetic-local-only'

PREVIEW=$(curl --fail-with-body -sS "$BUNKERKARTET_URL/api/admin/imports/preview" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @package.json)
PREVIEW_HASH=$(printf '%s' "$PREVIEW" | python3 -c 'import json, sys; print(json.load(sys.stdin)["preview_hash"])')

curl --fail-with-body -sS "$BUNKERKARTET_URL/api/admin/imports/commit" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "X-Import-Preview: $PREVIEW_HASH" \
  -H 'Content-Type: application/json' \
  --data-binary @package.json

# Identisk retry bruker samme batch_id, pakke og preview-hash.
curl --fail-with-body -sS "$BUNKERKARTET_URL/api/admin/imports/commit" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "X-Import-Preview: $PREVIEW_HASH" \
  -H 'Content-Type: application/json' \
  --data-binary @package.json

# Readback fra den lokale katalogen etter commit.
curl --fail-with-body -sS "$BUNKERKARTET_URL/api/sites" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Bruk bare en ekstern URL og produksjonstoken når du bevisst skal gjøre en
autorisert operasjon mot den tjenesten. Eksempelet setter aldri en
produksjons-URL som standard.

JSON-kontrakten kan hentes fra
`$BUNKERKARTET_URL/api/imports/schema` med samme bearer-token.

Ikke finn på koordinater, kopier hele artikler eller last opp bilder til
pakken. Lagre kilde-URL, kort utdrag og begrunnelse. Et omtrentlig punkt skal
ikke presenteres som eksakt, og en rute eller en `public`-merking betyr ikke at
det er lov eller trygt å gå inn på stedet.

## Produksjon og sikkerhet

GitHub Actions kjører tester, validerer frontend-syntaks og publiserer et
Docker-image til GHCR. `deploy/site.yml` verifiserer at commit-taggen peker på
riktig image-digest før containeren startes. Caddy terminerer TLS på
`bunker.reidar.tech`, mens applikasjonen er bundet til loopback på serveren.

Ikke legg `ADMIN_TOKEN`, `ORS_API_KEY`, andre nøkler eller produksjonsdata i
Git. Eksempelfilene i `deploy/` inneholder bare plassholdere.

## Kvalitetssjekk

```sh
.venv/bin/python -m pytest -q
```

Før publisering bør også JavaScript-syntaks og diff kontrolleres med prosjektets
CI-oppsett. Feltobservasjoner skal gjøres i dagslys fra offentlig og stabilt
område. Ikke gå inn, klatre, grave, bryte opp porter eller flytte gjenstander.
