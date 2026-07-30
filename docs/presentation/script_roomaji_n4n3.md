

### 1. あいさつ・導入（30秒〜45秒）

Minasan, konnichiwa. Kyō wa, kōbai-bu no tame ni tsukutta shisutemu wo,
kantan ni shōkai shimasu.
Kore wa, PC no buhin ga korekara dorekurai hitsuyō ni naru ka wo yosō
shite, chūmon wo tasukeru shisutemu desu.
Jikan ga mijikai node, kyō wa jissai no gamen wo mise nagara, hanashi wo
susumemasu.

EN: Hello everyone. Today I'll briefly introduce a system built for
the Purchasing department. It forecasts how much of each PC part will be
needed going forward, to help with ordering decisions. Since time is
short, I'll move through this while showing the actual screens.

### 2. なぜ作ったか（1分）

Ima, yosoku wa zenbu tesagyō desu. Keiei kikaku-bu ga yon-tsu no buhin
(CPU, mazābōdo, GPU, kēsu) wo keikaku shite, kōbai-bu ga sore wo motoni,
hoka no buhin (memori, HDD, PSU nado) wo keikaku shimasu.

Riyū wa mittsu arimasu.

Ichi: subete Excel de tesagyō nanode, dare ga, itsu, nani wo kaeta ka
kiroku ga nokorimasen. Hito no misu mo okorimasu.

Ni: yosoku ga attetta ka machigatta ka, ato de kakunin suru shikumi ga
arimasen. Dakara, seido wo agete iku koto ga muzukashii desu.

San: CPU to mazābōdo no yō ni, issho ni tsukau buhin dōshi no jusū ga,
atte inai koto ni kizukanai koto ga arimasu.

Kono shisutemu wa, kono mittsu no mondai wo sukoshi zutsu jidōka shite,
kaiketsu suru tame ni tsukurimashita.

**EN:** Right now, forecasting is entirely manual. Planning plans the 4
parts (CPU, motherboard, VGA, case), and Purchasing uses that plan as the
basis to plan the other parts (memory, HDD, PSU, etc.).

There are three reasons this system was needed:
1) Everything is manual Excel work, so there's no record of who changed
   what and when — and human error happens.
2) There's no way to check afterward whether a forecast was right or
   wrong, so it's hard to improve accuracy over time.
3) It's easy to miss when demand for parts that go together (like CPU and
   motherboard) doesn't line up.

This system was built to gradually automate and solve these three
problems.

---

### 3. 目的（1分）

Mokuteki wa mittsu arimasu.

Ichi: aru dēta (ONE keikaku, SAP zaiko, hanbai keikaku) wo yomu dake de
tsukatte, jidō de yosoku wo tsukurimasu. Dēta wo kaki-kaemasen — yomu dake
desu, anzen desu.

Ni: yosoku ga attetta ka, machigatta ka, ato de kakunin dekiru yō ni
shimasu.

San: itsu, dore dake chūmon suru ka teian shimasu. Demo, saigo no handan wa
itsumo hito ga shimasu. AI ga jidō de chūmon suru koto wa arimasen.

*(Koko wa taisetsu desu. Mō ichido iimasu: "AI wa teian dake, kettei wa
hito.")*

**EN:** Three goals:
1) Use existing data (ONE plan, SAP stock, sales plan) read-only to
   auto-generate forecasts. It never writes back to the source data — read
   only, so it's safe.
2) Make it possible to check afterward whether a forecast was accurate.
3) Suggest when and how much to order — but the final decision is always
   made by a human. The AI never places orders automatically.
*(This part matters — repeat it: "AI only suggests, humans decide.")*

### 4. ダッシュボード（1分、シンプルに）

Kore ga, saisho no gamen — "dasshubōdo" desu.
Koko wa, shinpuru na gamen desu.
Ima made no henkō no kiroku (kansa rogu) to, dēta zentai no basho
(mappingu) ga, koko de mieru dake desu.
Kuwashii hanashi wa, tsugi no gamen de shimasu.

EN: This is the first screen — the "Dashboard." It's kept simple on
purpose. Here you can just see the record of past changes (the audit
log) and an overview of where all the data comes from (the mapping).
The detailed content comes in the next screen.

---
### 5. 需要予測（メイン、3分）
Tsugi wa, "需要予測" (Juuyou yosoku) no gamen desu. Kore ga, ichiban
taisetsu na kinō desu.

Buhin goto ni, raigetsu kara no yosoku sūryō ga, ichiran de miemasu.
Koko no bubun ga, "keikaku" to "jisseki" wo shūgoto ni kuraberu tokoro
desu. SAP kara totta hontō no jisseki to, keikaku no kazu ga, narande
imasu. Dakara, zure ga sugu wakarimasu.
Ima tsukawarete iru buhin (akutibu pātsu) mo, koko de zenbu miemasu.

Zaiko (ima aru buhin no kazu) to, yosoku no juyō kara, itsu, dore dake
chūmon sureba ii ka, jidō de teian shimasu.
Lead time (chūmon kara todoku made no jikan) to, MOQ (saitei chūmon
sūryō) mo, kono teian ni chanto hairimasu.
Demo, kore mo teian dake desu. Saigo wa, hito ga kakunin shite kara
chūmon shimasu.

Tsumari, kono gamen dake de, "nani wo" "itsu" "dorekurai" chūmon sureba
ii ka, wakarimasu.

EN: Next is the "Demand Forecast" screen — the most important
feature. For each part, you see a list of forecasted quantities from
next month onward. This part compares "plan" vs "actual" week by week —
the real actual numbers from SAP are lined up right next to the planned
numbers, so you can immediately spot any gap. All parts currently in use
(active parts) are also visible here.

Based on current stock and forecasted demand, the system automatically
suggests when and how much to order. Lead time (how long delivery takes)
and MOQ (minimum order quantity) are both properly factored into this
suggestion. But again, it's just a suggestion — a person always reviews
and confirms before actually placing the order.

---
### 6. ミスマッチ検出（1分半）

Tsugi wa, "ミスマッチ検出"(Misumacchi kenshutsu)  no gamen desu.
Kore wa, keikaku to jisseki no aida ni, mondai ga nai ka wo, jidō de
chekku suru kinō desu.
Hito dake dewa kizukinikui zure wo, hayaku mitsukete, ōkii mondai ni naru
mae ni taiō dekimasu.

EN: Next is "Mismatch Detection." This automatically checks whether
there's a problem between plan and actual. It catches gaps that are hard
for a person to notice alone, early — before they become a bigger
problem.

---
### 7. 調整と監査ログ(Chousei to kansa rogu)（1分）

Dareka ga, yosoku no kazu wo te de naoshitara, sono henkō wa, kanarazu
kansa rogu ni kiroku saremasu.
Dare ga, itsu, naze kaeta ka, ato de itsu demo kakunin dekimasu.

EN: Whenever someone manually adjusts a forecast number, that change
is always recorded in the audit log. Who changed it, when, and why — you
can check this anytime afterward.

---
### 8. OpenAI APIについて（補足、1分）

Saigo ni, hitotsu hosoku desu.
CPU to mazābōdo no, kumiawase ga atte iru ka wo shiraberu tame ni, OpenAI
no API wo tsukatte imasu.
AI ga, kumiawase no kōho wo teian shite, sore wo hito ga kakunin shite
kara kimemasu.
AI dake de kimeru koto wa arimasen.

EN: One last note: to check whether CPU and motherboard combinations
are compatible, we use the OpenAI API. The AI proposes candidate
combinations, and a human reviews and confirms them before anything is
finalized. The AI never decides on its own.

---
### 9. 終わりに（30秒）

Isoi-ashi de no shōkai deshita ga, kono shisutemu no mokuteki wa, itsumo
onaji desu.
Dēta wo tsukatte, chūmon no handan wo tasukete, shigoto wo motto
wakariyasuku, seikaku ni suru koto desu.
Go-shitsumon ga areba, dōzo.

EN: That was a quick tour, but the purpose of this system is always
the same: use data to support ordering decisions, and make the work
clearer and more accurate. If you have any questions, please go ahead.