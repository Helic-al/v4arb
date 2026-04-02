import os
import time

import requests
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

load_dotenv("./.env")

DISCORD_URL = os.environ.get("DISCORD_URL")

MAIN_ACCOUNT_ADDRESS = os.environ.get("ARB_WALLET_ADDRESS")


def sendDiscord(message):
    try:
        data = {"content": message}
        requests.post(DISCORD_URL, json=data)
    except:
        pass


class HyperliquidOrderManager:
    def __init__(self, account, info=Info, exchange=Exchange):
        self.account = account
        self.info = info
        self.exchange = exchange
        self.coin = "ETH"  # 取引するコイン

        # ★ここを明確に分離設定！
        # ETHの価格は $3200.5 のように 0.1ドル刻み (USD単位)
        self.PRICE_TICK_USD = 0.1

        # ETHの数量は 0.0123 ETH のように 小数点4桁まで (ETH単位)
        self.SIZE_DECIMALS = 4

    def get_eth_price(self):
        """
        Hyperliquidから最新価格を取得する (最軽量・最速)
        endpoint: /info, type: "allMids"
        """
        try:
            # SDKに all_mids メソッドがあればそれを使うのが楽ですが、
            # info.post("/info", {"type": "allMids"}) を直接叩くイメージです

            # SDKの info インスタンス経由での呼び出し例
            # (SDKのバージョンによってメソッド名が違う場合がありますが、基本的な通信は以下)
            all_mids = self.info.all_mids()

            # 文字列で返ってくることが多いのでfloatに変換
            eth_price = float(all_mids["ETH"])

            return eth_price

        except Exception as e:
            print(f"⚠️ 価格取得エラー: {e}")
            # エラー時はNoneを返すか、前回の値を返すなどの処理
            return None

    def get_aggressive_price(self, is_buy):
        """価格(USD)を計算するロジック"""
        l2_data = self.info.l2_snapshot(name=self.coin)

        # これらは全て「ドル($)」
        best_bid_usd = float(l2_data["levels"][0][0]["px"])
        best_ask_usd = float(l2_data["levels"][1][0]["px"])
        spread = best_ask_usd - best_bid_usd

        # ★ここで足し算するのは「ドル($)」同士である必要がある
        # OK: $3200 + $0.1 = $3200.1
        # 単位 ($0.1)
        # 3. 判定ロジック
        # 誤差(浮動小数点)を考慮して、Tick(0.1)より少し大きいかで判定
        # 「スプレッドが 0.11ドル 以上あるか？」
        is_spread_wide_enough = spread > (self.PRICE_TICK_USD * 1.01)

        if is_buy:
            # --- 買いの場合 ---
            if is_spread_wide_enough:
                # 隙間がある → 1ティック上に割り込む (2935.5 -> 2935.6)
                # ※ここに来るのは Spreadが0.2以上の時だけなので、Ask(2935.7以上)にはぶつからない
                target = best_bid_usd + self.PRICE_TICK_USD
                print(
                    f"⚡ Aggressive Buy: {best_bid_usd} -> {target} (Spread: {spread:.2f})"
                )
                return target
            else:
                # 隙間がない(0.1しかない) → 割り込めないので Best Bid に並ぶ
                print(f"🛡️ Tight Spread ({spread:.2f}). Using Best Bid: {best_bid_usd}")
                return best_bid_usd

        else:
            # --- 売りの場合 ---
            if is_spread_wide_enough:
                # 隙間がある → 1ティック下に割り込む
                target = best_ask_usd - self.PRICE_TICK_USD
                print(
                    f"⚡ Aggressive Sell: {best_ask_usd} -> {target} (Spread: {spread:.2f})"
                )
                return target
            else:
                # 隙間がない → Best Ask に並ぶ
                print(f"🛡️ Tight Spread ({spread:.2f}). Using Best Ask: {best_ask_usd}")
                return best_ask_usd

    def get_best_market_price(self, is_buy):
        """
        現在の板情報から、自分の注文を置くべき「最良価格」を取得する
        is_buy: Trueならロング(Best Bid)、Falseならショート(Best Ask)
        """
        # L2スナップショット（板情報）を取得
        l2_data = self.info.l2_snapshot(name=self.coin)

        # levels[0] = Asks (売り板), levels[1] = Bids (買い板)
        # 構造: [['price', 'size'], ['price', 'size'], ...]

        if is_buy:
            # 買いたいときは「買い板の一番上（Best Bid）」に並ぶ
            # ※もっと早く約定させたいなら +0.05 などを足す(ペニージャンプ)
            best_bid_price = float(l2_data["levels"][1][0]["px"])
            return best_bid_price
        else:
            # 売りたいときは「売り板の一番下（Best Ask）」に並ぶ
            best_ask_price = float(l2_data["levels"][0][0]["px"])
            return best_ask_price

    def adjust_precision(self, value, precision):
        """価格や数量を、APIが受け付ける桁数に丸める"""
        # 簡易的な実装。実際はmeta情報を取得してtick_sizeで丸めるのがベスト
        return round(value, precision)

    def place_maker_order(self, size, is_buy):
        """
        指値注文(Maker)を実行する
        """
        # 1. 価格の決定
        target_price_usd = self.get_aggressive_price(is_buy)

        # 価格の丸め (ETHは通常 小数点以下1桁〜2桁。ここでは仮に1桁とする)
        # ※本来は info.meta() から sz_decimals を取得して動的に決めるべき
        limit_px = self.adjust_precision(target_price_usd, 1)

        # 数量の丸め (ETHは通常 小数点以下4桁など)
        sz = self.adjust_precision(size, 4)

        print(
            f"🚀 Placing Maker Order: {'BUY' if is_buy else 'SELL'} {sz} ETH @ ${limit_px}"
        )

        # 2. 注文パラメータの作成
        order_type = {
            "limit": {
                "tif": "Alo"  # ★最重要: Add Liquidity Only (Post-Only)
            }
        }

        # 3. 注文送信
        try:
            result = self.exchange.order(
                name=self.coin,
                is_buy=is_buy,
                sz=sz,
                limit_px=limit_px,
                order_type=order_type,
                reduce_only=False,  # ヘッジでショートを積む場合はFalse。決済専用ならTrue
            )

            # 結果の確認
            status = result["response"]["data"]["statuses"][0]
            if "error" in status:
                # Alo指定でTakerになりそうな場合などはここに来る
                print(f"⚠️ Order Failed/Cancelled (Post-Only triggered?): {status}")
                return None
            else:
                print(f"✅ Order Placed successfully: {status}")
                return status

        except Exception as e:
            print(f"❌ Error placing order: {e}")
            return None

    def execute_smart_hedge(
        self,
        size,
        panic_size,
        is_buy,
        calcRawDelta,
        panic_threshold,
        max_retries=5,
        wait_seconds=30,
    ):
        """
        スマート・ヘッジ実行ロジック:
        1. Post-Onlyエラーなら、最新価格で再発注 (max_retries回まで)
        2. 板に乗ったら(Resting)、wait_seconds秒待つ
        3. それでもダメなら、キャンセルして成行(Market)で約定させる
        """

        print(f"🛡️ スマートヘッジ開始: {'買い' if is_buy else '売り'} {size} ETH")
        # 緊急成り行き実行フラグ
        panic_triggered = False

        # --- Phase 1: 指値(Maker)での挑戦ループ ---
        for i in range(max_retries):
            print(f"🔄 指値トライ {i + 1}/{max_retries}回目...")

            # 注文を実行 (内部で最新のBest Bid/Askを取得している前提)
            result = self.place_maker_order(size, is_buy)

            # ----------------------------------------------------
            # パターンA: Post-Onlyエラー (弾かれた)
            # ----------------------------------------------------
            # resultがない、または 'resting' も 'filled' も含まれない場合はエラー扱い
            if not result or ("resting" not in result and "filled" not in result):
                print(
                    "⚠️ Post-Onlyにより弾かれました (Price moved)。1秒待機してリトライします。"
                )
                time.sleep(1)
                continue  # 次のループ(i+1)へ

            # ----------------------------------------------------
            # パターンB: 即約定 (Filled) - ラッキー
            # ----------------------------------------------------
            if "filled" in result:
                print("✅ 指値が即座に約定しました！(Maker成功)")
                sendDiscord("✅ 指値が即座に約定しました！(Maker成功)")
                return "MAKER_FILLED_INSTANT"

            # ----------------------------------------------------
            # パターンC: 板に乗った (Resting) - 待機フェーズへ
            # ----------------------------------------------------
            if "resting" in result:
                oid = result["resting"]["oid"]
                print(f"⏳ 板に乗りました (OID: {oid})。{wait_seconds}秒待ちます...")

                # 指定時間待つ
                # time.sleep(wait_seconds)

                # v6実装 緊急脱出用
                # --- ★ここが変更箇所: スリープではなく監視ループ ---
                start_time = time.time()

                while (time.time() - start_time) < wait_seconds:
                    # 1. 生データのチェック (緊急脱出判定)
                    try:
                        # 生価格を取得 (クラス内のメソッドを利用)
                        current_raw_price = self.get_eth_price()
                        # 生デルタを計算 (LPFを通さない生の変動を見る)
                        current_delta = calcRawDelta(current_raw_price)

                        # パニック判定: デルタが閾値を超えたら緊急脱出
                        if abs(current_delta) > panic_threshold:
                            print(
                                f"🚨 【緊急】待機中に相場急変！生デルタ: {current_delta:.2f}"
                            )
                            print("⚡ 指値をキャンセルして成行へ移行します(Bailout)...")
                            sendDiscord(
                                "⚡ 指値をキャンセルして成行へ移行します(Bailout)..."
                            )
                            # キャンセル実行
                            cancel_result = self.exchange.cancel(self.coin, oid)
                            if cancel_result["status"] == "ok":
                                panic_triggered = True
                                break  # 監視ループを抜ける -> Phase 2(成行)へ
                            else:
                                print("⚠️ キャンセル失敗(約定した可能性があります)")
                                # キャンセル失敗時は念の為ブレイクせず、下の約定確認へ流すのが安全

                    except Exception:
                        # エラーでも止まらずに待機を続ける(APIエラー等で止まると困るため)
                        # print(f"⚠️ 監視中エラー(無視): {e}")
                        pass
                        # --- 監視ループ終了後の判定 ---

                    if panic_triggered:
                        # パニックでキャンセル成功 -> ループを抜けて成行へ
                        break

                    # 2. 短いスリープ (1秒後にまたチェック)
                    time.sleep(1)

                # タイムアウトした場合 -> 通常の約定確認プロセス
                print("⌛ 待機時間終了。ステータスを確認します...")

                # --- 待機後の運命判定 ---

                # 1. 現在の「未約定注文リスト」を取得
                open_orders = self.info.open_orders(MAIN_ACCOUNT_ADDRESS)

                is_still_open = False

                # 2. リストの中に自分の注文があるか探す
                for order in open_orders:
                    if order["oid"] == oid:
                        # ★修正: ここでは「まだ残っている」ことだけを確認する
                        # 「約定しました」とは表示しない！
                        is_still_open = True
                        break

                # 3. 判定ロジック
                if not is_still_open:
                    # リストにない ＝ 消えた ＝ 約定した（成功）
                    print("✅ 待機中に約定しました！(Maker成功)")
                    sendDiscord("✅ 指値が約定しました！(Maker成功)")
                    return "MAKER_FILLED_WAIT"

                else:
                    # リストにある ＝ まだ残っている ＝ タイムアウト（失敗）
                    # ★ここで初めて「売れ残り」の処理をする
                    print("⚡ タイムアウト！指値をキャンセルして成行へ移行します。")
                    try:
                        self.exchange.cancel(self.coin, oid)
                    except Exception as e:
                        print(f"⚠️ キャンセルエラー(既に約定した可能性あり): {e}")

                    # ループを抜けて、下の成行処理へ進む
                    break

        # ループを抜けてきたということは、以下のどちらか
        # 1. max_retries回すべてPost-Onlyで弾かれた
        # 2. 板に乗ったがタイムアウトしてキャンセルした

        # --- Phase 2: 成行(Taker)での強制執行 ---
        print("🚀 成行注文(Market Order)を実行します...")
        try:
            # Hyperliquid SDKの成行注文関数
            if panic_triggered:
                market_result = self.exchange.market_open(self.coin, is_buy, panic_size)

            else:
                market_result = self.exchange.market_open(self.coin, is_buy, size)
                print(f"✅ 成行注文完了: {market_result['status']}")
                sendDiscord(f"✅ 成行注文完了: {market_result['status']}")
            return "TAKER_FILLED"

        except Exception as e:
            print(f"❌ 成行注文も失敗しました (致命的エラー): {e}")
            sendDiscord(f"❌ 成行注文も失敗しました (致命的エラー): {e}")
            return "FAILED"
