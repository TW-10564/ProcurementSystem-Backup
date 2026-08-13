# Purojekuto Gaiyō — ONE Tōgō / Juyō Yosoku Pōtaru

Kono shisutemu ga nan de aru ka, dēta ga hashi kara hashi made dō nagareru
ka, kaku rojikku ga kōdo no doko ni aru ka o matometa mono desu. Hibi no
kaihatsu komando ya deploy-ji no chūiten, zettai ni mamoru beki seiyaku wa
repository chokka no `CLAUDE.md` o sanshō shite kudasai. Kono fairu wa sono
naiyō no rōmaji-ban desu. Nihongo (kanji/kana) ban wa
`docs/PROJECT_OVERVIEW_JA.md`, eigo-ban wa `docs/PROJECT_OVERVIEW.md` ni
arimasu.

## Kore wa Nani ka

Sādowēbu no PC parts kōbai o shien suru, AI ni yoru kōbai ishikettei sapōto
shisutemu desu. NAS jō no mittsu no yomitori-senyō dēta sōsu — ONE no
busshu-yosoku / buhin-masutā Excel shisutemu, SAP zaiko (shūji ZM13
chūshutsu), Kikaku-bu no hanbai-keikaku fairu — o yomikomi, SKU tan'i no
juyō yosoku, hachū sūryō no teian, CPU/MB/VGA/Case no gokansei (kompatibiriti)
chekku, hito ni yoru chōsei / seido torakkingu o okonaimasu. SAP e no
kakimodoshi wa issai okonawazu, kikai gakushū (ML) mo shiyō shimasen
(tōkei-teki bēsurain + bakku-tesuto nomi). Subete no teian wa, hito ga
kakunin shite hajimete imi o motimasu.

Izen wa "Phase-1 Excel jidōka POC" (`consolidation/`) to iu zen-dankai no
mono ga arimashita ga, 2026-07-21 ni kono repository kara sakujo-zumi de,
kono phase ni wa fukumaremasen.

## Dēta no Nagare (Endo-tsū-endo)

```
NAS (ONE Excel fairu, SAP ZM13, Kikaku-bu no hanbai-keikaku fairu)
        │  nas_sync.py ga data/ e mirā-ringu (matawa ONEFCST_NAS_MODE=mount)
        ▼
ingestion/  (sōsu-goto no pāsā: one_master, sap, planning1/4, plan1_chain,
             note_plan, notepc_forecast/seasonality, keisuu, mb_procurement,
             name_crosswalk, subfiles)
        ▼
registry/build.py  — meimei taikei o matagu betsumei (eriasu) kaiketsu
                      (musubi-tsuke kī: adana), fu-seigō no kenshutsu
        ▼
build.py (M1)  — zen sōsu o tōgō rejisutorī e torikomi, Gate A tōtsūgō
                  repōto o shutsuryoku
        ▼
onefcst.db  (kaihatsu kankyō wa SQLite / honban wa Postgres.
             db.py ga DATABASE_URL de hantei)
        ▼
forecasting.py  — SKU-goto no yosoku seisei:
   engine/baseline.py   itsutsu no kōho moderu (kisetsu-sei / gensui
                         torendo / flat3 / flat6 / median6) + combo
                         (tō-kajū heikin)
   engine/seasonality.py, chain.py, transition.py, weekly.py
   → SKU-goto ni roku-kagetsu no wōku-fowādo bakku-tesuto de saiyō
     moderu o sentei
   → shōsha moderu ga jūni-kagetsu saki made yosoku
   tier2_forecasting.py + engine/tier2.py  — Kikaku-bu ga keikaku shinai
                         hinmoku (RAM, PSU, kēburu, FAN, SSD/HDD, OS,
                         hōsō nado) muke no sōchaku-ritsu bēsu hasei yosoku
   engine/compat.py     — Tier-2 no sūryō o Tier-1 (CPU/MB/VGA/Case)
                         keikaku ni renmoku saseru BOM / gokansei chekku
        ▼
advisory/  — yuiitsu no kakikomi kanō reiyā: adjustments.py (hito ni yoru
             uwagaki), rules.py + llm_rules.py (AI ga shitagaki suru
             gokansei rūru, hito no kakunin furō tsuki), notifications.py
        ▼
forecast_views.py  — yomitori-gawa API kumitate: yosoku + zaiko + chōsei
                      kekka o ketsugō shi, samarī / ichiran / shōsai /
                      hachū teian o seisei
engine/stock_projection.py  — zaiko o juyō ni taishite shōrai e tōei shi,
                      hachū taimingu o MOQ to rīdo-taimu ni awasete sanshutsu
                      (nyūka yotei PO / yusōchū zaiko wa moderu-ka shite inai)
        ▼
verify/  — seido / QA gēto: reconciliation.py (Gate A), parity.py
           (Gate B, seru tan'i), backtest.py (Gate C), fva.py
           (Gate D, yosoku fuka-kachi sukoaringu), mismatch.py
           (teiku-oa-pei no fu-seigō gēto)
        ▼
api/main.py (FastAPI)  →  furonto-endo (React/Vite)
```

**Ni-dankai ākitekucha** ga kihon no kangae-kata desu. **Kikaku-bu ga
chokusetsu keikaku suru** no wa CPU/MB/VGA/Case (juyō o kenin suru
kategorī) de, sore igai wa sōchaku-ritsu / gokansei rūru o tōshite
**kōbai-bu muke ni hasei** sasemasu.

## Bakku-endo no Kōdo Kōsei (`app/backend/onefcst/`)

| Pasu | Yakuwari |
|---|---|
| `config.py` | Sōsu pasu (kankyō hensū de jōgaki kanō), `TIER1_CATEGORIES`, `find_planning_file()` |
| `models.py` | SQLAlchemy no zen sukīma — hinmoku, betsumei, keikaku-chi, zaiko jōtai, yosoku, chōsei, gokansei rūru, seido sukoa |
| `db.py` | Enjin / sesshon. `DATABASE_URL` ni yori SQLite ka Postgres o hantei, `pool_pre_ping` |
| `build.py` | M1 torikomi shori → tōgō rejisutorī + Gate A repōto |
| `rebuild_preserving.py` | `build` no sai-kōchiku rappā — sai-kōchiku-ji mo hito ga nyūryoku shita dēta (chōsei / gokansei rūru / kansa rogu) o hoji. Postgres de wa `pg_dump -Fc` de bakkuappu |
| `scheduler.py` | `forecasting.generate()` o teiki jikkō suru bakku-guraundo rūpu |
| `forecasting.py` | SKU-goto no yosoku seisei, go-kōho + combo no kyōgō hōshiki |
| `forecast_views.py` | Yomitori-gawa API kumitate — samarī / ichiran / shōsai, chōsei no han'ei, hachū teian |
| `tier2_forecasting.py` | Tier-2 (hasei / sōchaku-ritsu) yosoku |
| `planner_accuracy.py` | Keikaku tantō-sha no yosoku o sunappushotto-ka shi, kako no seido o tsuiseki |
| `ingestion/` | NAS sōsu-goto no pāsā |
| `registry/build.py` | Betsumei kaiketsu, fu-seigō kenshutsu |
| `engine/` | Jun-suikan-sū (I/O nashi) — baseline, seasonality, chain, transition, weekly, tier2, compat, stock_projection, fva |
| `advisory/` | Kakikomi kanō reiyā — adjustments, rules, llm_rules, notifications |
| `verify/` | Seido / QA gēto (jōki paipurain sanshō) |
| `export/excel.py` | Byū-goto no seikei-zumi Excel ekusupōto |
| `api/main.py` | FastAPI rūto |
| `api/auth.py` | Kyōyū `X-API-Key` izon kankei |
| `api/entra.py` | Entra ID JWT kenshō (tenanto JWKS ni taisuru RS256) |
| `api/schemas.py` | Pydantic no rikuesuto / resuponsu moderu |
| `tests/` | Sabushisutemu / shūsei-goto ni ichi-fairu, pytest de jikkō |

Kōdo ni kumikomarete iru kichi no chūiten: `verify/order_suggestions.py`
no `_future_demand()` wa `forecast_views.order_suggestions()` to wa
**betsu no kōdo pasu** desu — ippō no juyō keisan rojikku o shūsei shite
mo, mō ippō ni wa jidō-teki ni wa han'ei saremasen (kakunin-zumi no kairi:
kata-hō wa `PlanValue` no namachi o yon-kagetsu-bun gōkei shi, mō kata-hō
wa jūni-kagetsu-bun no furu-paipurain shutsuryoku o tsukau).

## Furonto-endo no Kōdo Kōsei (`app/frontend/src/`)

| Pasu | Yakuwari |
|---|---|
| `pages/` | `Dashboard`, `Forecast`, `Mismatch`, `Accuracy`, `Adjustments`, `CompatReview`, `Notifications`, `Landing` |
| `components/Layout.tsx` | Apuri no soto-waku — heddā, saido-nabi, `header-logo-text` no rogo moji |
| `api/client.ts` | Axios insutansu, `VITE_API_BASE` kara `BASE_URL`, `X-API-Key` heddā |
| `api/types.ts` | Bakku-endo no Pydantic sukīma ni taiō suru TS-gata |
| `i18n/translations.ts` | Nihongo / eigo no moji-retsu tēburu (`brand_name`, `dash_api_error` nado) |
| `auth/msal.ts`, `auth/AuthGate.tsx` | Entra SSO muke MSAL PKCE furō (paburikku kuraianto, shīkuretto fuyō) |

## Ninshō (Auth)

`ONEFCST_ENTRA_TENANT_ID` / `ONEFCST_ENTRA_CLIENT_ID` no settei umu de
ni-mōdo o kirikaemasu:
- **Mi-settei** (kaihatsu kankyō no defoluto): kyōyū `X-API-Key` heddā nomi.
- **Settei-zumi**: subete no rikuesuto ni Microsoft tōkun ga hisu. SPA wa
  paburikku kuraianto to shite burauza-nai de PKCE o jikkō shi, bakku-endo
  wa kenshō nomi okonau (tenanto JWKS ni taisuru RS256 + aud/iss/tid/exp).
  "Dare ga nani o dekiru ka" no jissō wa mada — `Identity.groups` /
  `.roles` wa shutoku sarete iru ga mishiyō.

## Dētabēsu

`db.py` ga `DATABASE_URL` ni yori bakku-endo o hantei shimasu. Mi-settei
nara `app/data/onefcst.db` no SQLite. Settei-zumi
(`postgresql+psycopg://…`) nara meiji-teki na pūru settei o motsu
Postgres 17. Ryōhō to mo onaji tesuto-sūto (`ONEFCST_TEST_DATABASE_URL`)
de dōsa kakunin shimasu. Atarashii kuerī kōdo de mamoru beki ihō-sei
(pōtabiriti) rūru: SQLite senyō kansū (`julianday` / `strftime`) wa
tsukawanai, NULL no narabi-jun wa kanarazu meiji-teki ni shitei suru
(`.nulls_first()` / `.nulls_last()` — SQLite to Postgres wa defoluto no
narabi-jun ga gyaku).

## Dipuroi (Deploy)

Hitotsu no maruchi-sutēji Docker imēji ga, 8600-ban pōto de API to
bilo-zumi SPA no ryōhō o haishin shi, Postgres mo dōkon shimasu. nginx wa
kontena-nai de wa naku hosuto-gawa de dōsa shimasu. uvicorn wākā wa
hitotsu nomi (futatsu ugokasu to sukejūrā ga futatsu ugoite shimau).
Kōdo o henkō shita baai wa kontena no saikidō ga hitsuyō desu.
`ONEFCST_API_KEY` no rōtēshon ni wa imēji no sai-birudo ga hitsuyō
(JS bandoru ni konpairu sarete umekomareru tame). CI
(`.github/workflows/ci.yml`) wa push / PR no tabi ni ryōhō no DB hōgen de
tesuto-sūto o jikkō shi, `npm run build` to `docker build` mo okonaimasu.
`deploy.yml` wa `main` de CI ga gurīn ni natta ato ni nomi, serufu-hosuto
rannā keiyu de DGX e dipuroi shimasu.

Mentenansu mōdo wa, NAS no sai-kōchiku shori kara wa ito-teki ni
**furemasen** — jissai no dipuroi-ji ni hito ga tedō de kirikaeru dake desu
(`onefcst.maintenance on/off`). Sai-kōchiku-chū wa, kawari ni
`/build/rebuild/status` ga kudō suru hi-burokkingu no "sai-kōchiku-chū"
hyōji bā ga demasu.
