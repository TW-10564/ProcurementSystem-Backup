# プロジェクト概要 — ONE 統合・需要予測ポータル

このシステムが何であるか、データが端から端までどう流れるか、各ロジックが
コード上のどこにあるかをまとめたものです。日々の開発コマンドやデプロイ時
の注意点、絶対に守るべき制約はリポジトリ直下の `CLAUDE.md` を参照してくだ
さい。本ファイルはその内容の説明版（日本語）です。英語版は
`docs/PROJECT_OVERVIEW.md` にあります。

## これは何か

サードウェーブのPCパーツ購買を支援する、AIによる購買意思決定サポートシス
テムです。NAS上の3つの読み取り専用データソース——ONEの部品予測／部品マス
ター Excelシステム、SAP在庫（週次ZM13抽出）、企画部の販売計画ファイル——
を読み込み、SKU単位の需要予測、発注数量の提案、CPU/MB/VGA/Caseの互換性
チェック、人による調整・精度トラッキングを行います。SAPへの書き戻しは一
切行わず、機械学習も使用しません（統計的ベースライン＋バックテストのみ）。
すべての提案は、人が確認して初めて意味を持ちます。

以前は「Phase-1 Excel自動化POC」（`consolidation/`）という前段階のもの
がありましたが、2026-07-21にこのリポジトリから削除済みで、本フェーズには
含まれません。

## データの流れ（エンドツーエンド）

```
NAS（ONE Excelファイル、SAP ZM13、企画部の販売計画ファイル）
        │  nas_sync.py が data/ にミラーリング（または ONEFCST_NAS_MODE=mount）
        ▼
ingestion/  （ソースごとのパーサー：one_master, sap, planning1/4, plan1_chain,
             note_plan, notepc_forecast/seasonality, keisuu, mb_procurement,
             name_crosswalk, subfiles）
        ▼
registry/build.py  — 命名体系をまたいだ別名（エイリアス）解決
                      （結合キー：綽名）、不整合の検出
        ▼
build.py (M1)  — 全ソースを統合レジストリへ取り込み、Gate A 突合レポートを出力
        ▼
onefcst.db  （開発環境はSQLite／本番はPostgres。db.pyが DATABASE_URL で判定）
        ▼
forecasting.py  — SKUごとの予測生成：
   engine/baseline.py   5つの候補モデル（季節性 / 減衰トレンド / flat3 /
                         flat6 / median6）＋ combo（等重み平均）
   engine/seasonality.py, chain.py, transition.py, weekly.py
   → SKUごとに6ヶ月のウォークフォワード・バックテストで最良モデルを選定
   → 勝者モデルが12ヶ月先まで予測
   tier2_forecasting.py + engine/tier2.py  — 企画部が計画しない品目
                         （RAM、PSU、ケーブル、FAN、SSD/HDD、OS、梱包等）
                         向けの装着率ベース派生予測
   engine/compat.py     — Tier-2の数量をTier-1（CPU/MB/VGA/Case）計画に
                         連動させるBOM／互換性チェック
        ▼
advisory/  — 唯一の書き込み可能レイヤー：adjustments.py（人による上書き）、
             rules.py + llm_rules.py（AIが下書きする互換性ルール、人の確認
             フロー付き）、notifications.py
        ▼
forecast_views.py  — 読み取り側API組み立て：予測＋在庫＋調整結果を結合し、
                      サマリー／一覧／詳細／発注提案を生成
engine/stock_projection.py  — 在庫を需要に対して将来へ投影し、発注タイミ
                      ングをMOQとリードタイムに合わせて算出
                      （入荷予定PO・輸送中在庫はモデル化していない）
        ▼
verify/  — 精度・QAゲート：reconciliation.py（Gate A）、parity.py
           （Gate B、セル単位）、backtest.py（Gate C）、fva.py
           （Gate D、予測付加価値スコアリング）、mismatch.py
           （テイクオアペイの不整合ゲート）
        ▼
api/main.py (FastAPI)  →  フロントエンド（React/Vite）
```

**2段階アーキテクチャ**が基本の考え方です。**企画部が直接計画する**のは
CPU/MB/VGA/Case（需要をけん引するカテゴリ）で、それ以外は装着率／互換性
ルールを通じて**購買部向けに派生**させます。

## バックエンドのコード構成（`app/backend/onefcst/`）

| パス | 役割 |
|---|---|
| `config.py` | ソースパス（環境変数で上書き可）、`TIER1_CATEGORIES`、`find_planning_file()` |
| `models.py` | SQLAlchemyの全スキーマ — 品目、別名、計画値、在庫状態、予測、調整、互換性ルール、精度スコア |
| `db.py` | エンジン／セッション。`DATABASE_URL` によりSQLiteかPostgresを判定、`pool_pre_ping` |
| `build.py` | M1取り込み処理 → 統合レジストリ＋Gate Aレポート |
| `rebuild_preserving.py` | `build` の再構築ラッパー — 再構築時も人が入力したデータ（調整／互換性ルール／監査ログ）を保持。Postgresでは `pg_dump -Fc` でバックアップ |
| `scheduler.py` | `forecasting.generate()` を定期実行するバックグラウンドループ |
| `forecasting.py` | SKUごとの予測生成、5候補＋comboの競合方式 |
| `forecast_views.py` | 読み取り側API組み立て — サマリー／一覧／詳細、調整の反映、発注提案 |
| `tier2_forecasting.py` | Tier-2（派生／装着率）予測 |
| `planner_accuracy.py` | 計画担当者の予測をスナップショット化し、過去の精度を追跡 |
| `ingestion/` | NASソースごとのパーサー |
| `registry/build.py` | 別名解決、不整合検出 |
| `engine/` | 純粋関数（I/Oなし） — baseline、seasonality、chain、transition、weekly、tier2、compat、stock_projection、fva |
| `advisory/` | 書き込み可能レイヤー — adjustments、rules、llm_rules、notifications |
| `verify/` | 精度／QAゲート（上記パイプライン参照） |
| `export/excel.py` | ビューごとの整形済みExcelエクスポート |
| `api/main.py` | FastAPIルート |
| `api/auth.py` | 共有 `X-API-Key` 依存関係 |
| `api/entra.py` | Entra ID JWT検証（テナントJWKSに対するRS256） |
| `api/schemas.py` | Pydanticのリクエスト／レスポンスモデル |
| `tests/` | サブシステム／修正ごとに1ファイル、pytestで実行 |

コードに組み込まれている既知の注意点：`verify/order_suggestions.py` の
`_future_demand()` は `forecast_views.order_suggestions()` とは**別の
コードパス**です — 一方の需要計算ロジックを修正しても、もう一方には自動
的には反映されません（確認済みの乖離：片方は `PlanValue` の生値を4ヶ月分
合計し、もう片方は12ヶ月分のフルパイプライン出力を使う）。

## フロントエンドのコード構成（`app/frontend/src/`）

| パス | 役割 |
|---|---|
| `pages/` | `Dashboard`、`Forecast`、`Mismatch`、`Accuracy`、`Adjustments`、`CompatReview`、`Notifications`、`Landing` |
| `components/Layout.tsx` | アプリの外枠 — ヘッダー、サイドナビ、`header-logo-text` のロゴ文字 |
| `api/client.ts` | Axiosインスタンス、`VITE_API_BASE` から `BASE_URL`、`X-API-Key` ヘッダー |
| `api/types.ts` | バックエンドのPydanticスキーマに対応するTS型 |
| `i18n/translations.ts` | 日本語／英語の文字列テーブル（`brand_name`、`dash_api_error` 等） |
| `auth/msal.ts`, `auth/AuthGate.tsx` | Entra SSO向けMSAL PKCEフロー（パブリッククライアント、シークレット不要） |

## 認証

`ONEFCST_ENTRA_TENANT_ID`／`ONEFCST_ENTRA_CLIENT_ID` の設定有無で2モード
を切り替えます：
- **未設定**（開発環境のデフォルト）：共有 `X-API-Key` ヘッダーのみ。
- **設定済み**：すべてのリクエストにMicrosoftトークンが必須。SPAはパブ
  リッククライアントとしてブラウザ内でPKCEを実行し、バックエンドは検証
  のみ行う（テナントJWKSに対するRS256＋aud/iss/tid/exp）。「誰が何をで
  きるか」の実装はまだ — `Identity.groups`／`.roles` は取得されているが
  未使用。

## データベース

`db.py` が `DATABASE_URL` によりバックエンドを判定します。未設定なら
`app/data/onefcst.db` のSQLite。設定済み（`postgresql+psycopg://…`）なら
明示的なプール設定を持つPostgres 17。両方とも同じテストスイート
（`ONEFCST_TEST_DATABASE_URL`）で動作確認します。新しいクエリコードで
守るべき移植性ルール：SQlite専用関数（`julianday`／`strftime`）は使わない、
NULLの並び順は必ず明示的に指定する（`.nulls_first()`／`.nulls_last()` —
SQLiteとPostgresはデフォルトの並び順が逆）。

## デプロイ

1つのマルチステージDockerイメージが、8600番ポートでAPIとビルド済みSPAの
両方を配信し、Postgresも同梱します。nginxはコンテナ内ではなくホスト側で
動作します。uvicornワーカーは1つのみ（2つ動かすとスケジューラーが2つ動
いてしまう）。コードを変更した場合はコンテナの再起動が必要です。
`ONEFCST_API_KEY` のローテーションにはイメージの再ビルドが必要（JSバンド
ルにコンパイルされて埋め込まれるため）。CI（`.github/workflows/ci.yml`）
はpush・PRのたびに両方のDB方言でテストスイートを実行し、`npm run build`
と `docker build` も行います。`deploy.yml` は `main` でCIがグリーンに
なった後にのみ、セルフホストランナー経由でDGXへデプロイします。

メンテナンスモードは、NASの再構築処理からは意図的に**触れられません** —
実際のデプロイ時に人が手動で切り替えるだけです
（`onefcst.maintenance on/off`）。再構築中は、代わりに
`/build/rebuild/status` が駆動する非ブロッキングの「再構築中」表示バーが
出ます。
