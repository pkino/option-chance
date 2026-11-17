"""Slack通知"""
import os
from typing import List, Optional
import json

try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class SlackNotifier:
    """Slack通知クラス"""

    def __init__(self, webhook_url: Optional[str] = None):
        """
        Args:
            webhook_url: SlackのWebhook URL（Noneの場合は環境変数から取得）
        """
        if not HAS_REQUESTS:
            raise ImportError("requests が必要です")

        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")

        if not self.webhook_url:
            raise ValueError("Slack Webhook URLが設定されていません")

    def send_message(self, text: str, blocks: Optional[List[dict]] = None) -> bool:
        """
        Slackにメッセージを送信

        Args:
            text: メッセージテキスト
            blocks: Slack Block Kit のブロック（オプション）

        Returns:
            成功したか
        """
        payload = {"text": text}

        if blocks:
            payload["blocks"] = blocks

        try:
            response = requests.post(
                self.webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()

            print(f"Slack通知送信成功: {text[:50]}...")
            return True

        except Exception as e:
            print(f"Slack通知送信エラー: {e}")
            return False

    # 種類ごとの見出し（gate.SIGNAL_KINDS と対応）
    SIGNAL_HEADERS = {
        "entry": "🔴 エントリーシグナル検出",
        "supply": "🟠 需給主導エントリー検出",
        "probe": "🟡 打診シグナル検出",
        "event_probe": "🗓️ イベント前打診",
    }

    def send_signal(
        self, kind: str, signal_text: str, option_candidates: Optional[List[dict]] = None
    ) -> bool:
        """
        シグナルを送信

        Args:
            kind: entry / supply / probe / event_probe
            signal_text: シグナルの説明テキスト
            option_candidates: 候補オプションのリスト（オプション）

        Returns:
            成功したか
        """
        header = self.SIGNAL_HEADERS.get(kind, "📊 シグナル検出")
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": signal_text}},
        ]

        # オプション候補があれば追加
        if option_candidates:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*推奨オプション候補:*"},
                }
            )

            for opt in option_candidates[:5]:  # 最大5つ
                premium = opt.get("premium")
                opt_text = (
                    f"• Strike: {opt.get('strike')} | "
                    f"Premium: {f'{premium:.2f}' if premium is not None else 'N/A'}円 | "
                    f"Delta: {opt.get('delta', 'N/A')} | "
                    f"DTE: {opt.get('dte')}日"
                )
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": opt_text}})

        return self.send_message(text=header, blocks=blocks)

    def send_entry_signal(self, signal_text: str, option_candidates: Optional[List[dict]] = None) -> bool:
        """エントリーシグナルを送信（互換用。send_signal("entry", ...) と同じ）"""
        return self.send_signal("entry", signal_text, option_candidates)

    def send_no_signal(self, date_str: str) -> bool:
        """
        シグナルなしを送信（デバッグ用・オプション）

        Args:
            date_str: 日付文字列

        Returns:
            成功したか
        """
        text = f"✅ {date_str}: エントリーシグナルなし（待機中）"
        return self.send_message(text)

    def send_error(self, error_message: str) -> bool:
        """
        エラーメッセージを送信

        Args:
            error_message: エラーメッセージ

        Returns:
            成功したか
        """
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "⚠️ エラー発生"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"```{error_message}```"}},
        ]

        return self.send_message(text="エラー発生", blocks=blocks)


def test_slack_notifier():
    """テスト用関数"""
    # 環境変数からWebhook URLを取得
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

    if not webhook_url:
        print("環境変数 SLACK_WEBHOOK_URL が設定されていません")
        print("テストをスキップします")
        return

    notifier = SlackNotifier(webhook_url)

    # テストメッセージを送信
    print("テストメッセージを送信中...")
    success = notifier.send_message("これはテストメッセージです")

    if success:
        print("✅ テスト成功！")
    else:
        print("❌ テスト失敗")


if __name__ == "__main__":
    test_slack_notifier()
