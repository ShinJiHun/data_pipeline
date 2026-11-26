"""
Slack 실패 알림 (Incoming Webhook).

.env 의 SLACK_WEBHOOK_URL 이 있으면 보내고, 없으면 로그만 남긴다.
메시지에는 키·토큰·계좌번호를 넣지 않는다 (호출하는 쪽에서 정리된 문구만 넘길 것).
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)


def notify_slack(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        logger.info("SLACK_WEBHOOK_URL 미설정 — Slack 알림 생략")
        return
    try:
        res = requests.post(url, json={"text": text}, timeout=10)
        if res.status_code != 200:
            logger.warning("Slack 알림 실패: HTTP %s", res.status_code)
    except requests.RequestException as e:
        logger.warning("Slack 알림 예외: %s", type(e).__name__)
