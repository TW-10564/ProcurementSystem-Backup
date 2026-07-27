# プレゼン構成案（スライドごとのメモ）／ Presentation Outline

各スライドに「言うこと」の骨子だけ書いてあります。スライド自体はご自身のフォーマットで
作成し、このメモを話す内容のベースにしてください。英語メモは補足用の短い要約です。

---

### スライド1：タイトル
需要予測ポータル（onefcst）— Phase 1 進捗報告
Romaji: Juyo Yosoku Pōtaru (onefcst) — Phase 1 Shinchoku Hōkoku
EN: Demand Forecasting Portal (onefcst) — Phase 1 Progress

---

### スライド2：背景（なぜやっているか）
Romaji: Haikei (Naze Yatteiru ka)
- 予測はExcelベースで手作業、部品ごとに担当者が分かれている
- 名寄せ（綽名）に時間がかかる、精度を後から検証できない
- CPU/マザーボードのような組み合わせ部品の需要食い違いに気づきにくい

話し方のコツ：ここは「困っていること」を具体的に言うと刺さる。数字より実感ベースでOK。

EN note: Manual, spreadsheet-driven forecasting; no accuracy tracking; cross-part mismatches go unnoticed.

---

### スライド3：目的とスコープ
Romaji: Mokuteki to Sukōpu
- 対象はPhase 1＝需要予測と精度検証のみ（Excel自動化・書き込みは対象外）
- 既存のONE計画・SAPなど全ソースは読み取り専用
- 最終判断は必ず人間、AIは提案どまり

EN note: Phase 1 = forecasting + accuracy tracking only. Read-only sources. Humans always confirm.

---

### スライド4：全体の処理の流れ（図解推奨）
Romaji: Zentai no Shori no Nagare (Zukai Suishō)
ソース取り込み → 名寄せ → 予測エンジン → 互換性チェック → 人による調整記録
→ 発注提案／不整合検知 → ダッシュボード → 精度検証

ここは図（フロー図）を1枚作ると伝わりやすいです。8段階を横並びの矢印で。

EN note: 8-stage pipeline; only the "human adjustment" stage writes anything — everything else is read + compute.

---

### スライド5：二段構えの予測（誰が何を計画しているか）
Romaji: Nidan-gamae no Yosoku (Dare ga Nani wo Keikaku Shiteiru ka)
- 経営企画部：CPU/マザーボード/VGA/ケースを計画
- 購買部：それ以外（RAM/PSU/ケーブル/SSD等）を1段目の予測から導出
- この体制自体は既存の業務フローをそのまま踏襲している

EN note: Two-tier — planning owns 4 demand-driving categories, purchasing derives the rest via attach-rate.

---

### スライド6：予測の仕組み（品目ごとに方式を競わせる）
Romaji: Yosoku no Shikumi (Hinmoku Goto ni Hōshiki wo Kisowaseru)
- 季節性／トレンド／直近3ヶ月平均／直近6ヶ月平均／中央値／複合平均、の候補を用意
- 品目ごとに過去実績でどれが一番当たるか検証してから選ぶ
- 機械学習は使わない方針（判断根拠を人が追えることを優先）

EN note: 5 candidate methods + a combo, competed per-SKU on historical hold-out. No ML — statistical only, by design (traceability).

---

### スライド7：精度検証結果（★発表直前に最新の数字に差し替え）
Romaji: Seido Kenshō Kekka
- カテゴリ集計：季節性方式 WAPE 10.95%（単純均等割り16.28%、前年同月13.59%を上回る）
- 品目単位：複合方式で WAPE 115.71%（改善前121.45%）
- 直近1ヶ月先だけなら WAPE 50.52%（実務で一番使う数字）
- 経営企画部自身の過去実績（デスクトップ9〜13%、ノートPC24%）と同水準

話し方のコツ：品目単位のWAPEが100%を超えているのは一見悪く見えるが、
「1品目あたりの月次需要はロットや突発発注でブレが大きい」という前提を先に言ってから出すこと。

EN note: Category-level beats naive baselines by 30-50%. SKU-level is noisy by nature (lumpy demand) — near-term (M+1) number is what buyers actually use.

---

### スライド8：見つかった実例（互換性チェック）
Romaji: Mitsukatta Jitsurei (Gokansei Chekku)
- AM4ソケットで、マザーボード需要がCPU需要を200〜360%上回る状態が
  2020年10月〜2026年2月まで39ヶ月続いていた
- 単発ではなく構造的な乖離。原因はまだ未確認（修理用在庫か、過剰計画かなど）
- 直近月（2026年7月）は全ソケットで±14%以内、緊急性は低い

EN note: Compat-mismatch gate found a real, persistent AM4 CPU/MB demand gap spanning 6 years — not asserted as a bug, flagged as a question for planning/purchasing.

---

### スライド9：発注提案
Romaji: Hacchū Teian
- 在庫＋予測需要から、発注時期・数量を提案
- 実データで検証：127品目中45品目で提案可能、うち47%が実発注の2倍以内
- 桁感覚としては使えるが、まだ無人で使える精度ではない→必ず人がレビュー

EN note: 47% of testable suggestions land within 2x of actual order qty — directional signal, not yet unreviewed-ready.

---

### スライド10：まだできていないこと（正直に）
Romaji: Mada Dekiteinai Koto (Shōjiki ni)
- 発注残（入荷予定PO）が反映されていない
- ①販売計画の名寄せが37%しか解決していない
- 品目単位の精度は経営企画部との直接比較データがまだ少ない

話し方のコツ：ここを隠さず出すと信頼される。「弱点も分かった上で運用している」という姿勢を見せる。

EN note: Known gaps, stated plainly — no in-transit PO modeling, low resolution on sales-plan ①, limited SKU-level comparison data yet.

---

### スライド11：次のステップ
Romaji: Tsugi no Suteppu
- 経営企画部・購買部でのパイロット運用開始
- 販売計画①の名寄せ精度改善
- 発注提案のチューニング
- 毎月の予測を記録し、実績と自動で突き合わせる仕組みは構築済み→今後精度が継続的に見える

EN note: Pilot with planning/purchasing, improve ① resolution, tune order suggestions; monthly planner-accuracy tracking already instrumented.

---

### スライド12：質疑応答
Romaji: Shitsugi Ōtō
EN: Q&A

---

## 話す上での注意点（自分用メモ）
- 数字は必ず最新のbacktest結果に更新してから使う（IMPLEMENTATION_PLAN.mdの数字は7/6時点、
  直近のコミットで精度改善が入っている）
- 「AIが自動で発注する」と誤解されないよう、毎回「人が確認する」ことを明言する
- 弱点（スライド10）を隠さない方が質疑で崩れにくい
