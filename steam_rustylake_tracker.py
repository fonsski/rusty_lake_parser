#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BUNDLE_ID = "3669"
DEFAULT_BUNDLE_NAME = "Rusty Lake Bundle"
DEFAULT_STEAM_CC = "us"
DEFAULT_STEAM_LANG = "english"
DEFAULT_TIMEOUT = 20
DEFAULT_STATE_FILE = "data/rustylake_state.json"
DEFAULT_MIN_DISCOUNT_PERCENT = 50
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)

PRICE_RE = re.compile(
    r"(?:[$€£¥₽₴₸₹₺₫₱₦]\s*[\d.,]+|[\d.,]+\s*[A-Za-z$€£¥₽₴₸₹₺₫₱₦]+)"
)
DISCOUNT_RE = re.compile(r"-?\d+%")


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_tag = tag
            return
        if tag in {"br", "div", "p", "li", "tr", "td", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tag == tag:
            self._ignored_tag = None
            return
        if tag in {"div", "p", "li", "tr", "td", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_tag is None and data.strip():
            self.parts.append(data)

    def lines(self) -> list[str]:
        text = "".join(self.parts)
        return [normalize_space(line) for line in text.splitlines() if normalize_space(line)]


@dataclass
class BundleSnapshot:
    fetched_at: str
    bundle_id: str
    bundle_name: str
    store_url: str
    current_price_text: str
    original_price_text: str | None
    discount_percent: int


def normalize_space(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def build_store_url(bundle_id: str, cc: str, lang: str) -> str:
    return f"https://store.steampowered.com/bundle/{bundle_id}/?cc={cc}&l={lang}"


def fetch_html(url: str, timeout: int) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def extract_value_before_label(
    lines: list[str], label: str, pattern: re.Pattern[str] | None = None
) -> str | None:
    normalized_label = normalize_space(label).lower()
    for index, line in enumerate(lines):
        normalized_line = normalize_space(line).lower()
        if normalized_line == normalized_label or normalized_label in normalized_line:
            for previous in range(index - 1, -1, -1):
                candidate = lines[previous]
                if not candidate:
                    continue
                if pattern is not None:
                    match = pattern.search(candidate)
                    if match:
                        return normalize_space(match.group(0))
                    continue
                if normalize_space(candidate).lower() != normalized_label:
                    return candidate
    return None


def extract_first_price(lines: list[str]) -> str | None:
    for line in lines:
        match = PRICE_RE.search(line)
        if match:
            return normalize_space(match.group(0))
    return None


def parse_discount_percent(value: str | None) -> int:
    if not value:
        return 0
    match = DISCOUNT_RE.search(value)
    if not match:
        return 0
    return abs(int(match.group(0).replace("%", "")))


def parse_bundle_snapshot(bundle_id: str, bundle_name: str, store_url: str, html: str) -> BundleSnapshot:
    extractor = TextExtractor()
    extractor.feed(html)
    lines = extractor.lines()

    current_price = extract_value_before_label(lines, "Your cost:", PRICE_RE)
    original_price = extract_value_before_label(lines, "Price of individual products:", PRICE_RE)
    discount_text = extract_value_before_label(lines, "Bundle discount:", DISCOUNT_RE)

    if current_price is None:
        current_price = extract_first_price(lines)
    if current_price is None:
        raise ValueError("Failed to parse the current bundle price from Steam page.")

    discount_percent = parse_discount_percent(discount_text)

    if original_price is None and discount_percent == 0:
        original_price = current_price

    return BundleSnapshot(
        fetched_at=utc_now_iso(),
        bundle_id=bundle_id,
        bundle_name=bundle_name,
        store_url=store_url,
        current_price_text=current_price,
        original_price_text=original_price,
        discount_percent=discount_percent,
    )


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def send_telegram_message(token: str, chat_id: str, text: str, timeout: int) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="ignore")
    parsed = json.loads(body)
    if not parsed.get("ok"):
        raise ValueError(f"Telegram API error: {parsed}")


def changed_fields(previous: dict[str, Any], current: BundleSnapshot) -> list[str]:
    changes: list[str] = []
    if previous.get("current_price_text") != current.current_price_text:
        changes.append(
            f"цена: {previous.get('current_price_text', 'неизвестно')} -> {current.current_price_text}"
        )
    if previous.get("original_price_text") != current.original_price_text:
        changes.append(
            "полная цена: "
            f"{previous.get('original_price_text', 'неизвестно')} -> {current.original_price_text or 'неизвестно'}"
        )
    if int(previous.get("discount_percent", 0)) != current.discount_percent:
        changes.append(
            f"скидка: {previous.get('discount_percent', 0)}% -> {current.discount_percent}%"
        )
    return changes


def should_notify(
    first_run: bool,
    changes: list[str],
    discount_percent: int,
    min_discount_percent: int,
    notify_on_any_change: bool,
    notify_on_first_run: bool,
    force_notify: bool,
) -> bool:
    if force_notify:
        return True
    if first_run:
        return notify_on_first_run or discount_percent >= min_discount_percent
    if notify_on_any_change and changes:
        return True
    return bool(changes and discount_percent >= min_discount_percent)


def build_message(
    current: BundleSnapshot,
    changes: list[str],
    min_discount_percent: int,
    first_run: bool,
    force_notify: bool,
) -> str:
    if force_notify:
        header = "Проверка уведомлений Rusty Lake"
    elif first_run:
        header = "Слежение за Rusty Lake Bundle запущено"
    elif current.discount_percent >= min_discount_percent:
        header = f"Большая скидка на {current.bundle_name}"
    else:
        header = f"Изменилась цена на {current.bundle_name}"

    lines = [f"<b>{escape(header)}</b>"]
    lines.append(f"Текущая цена: <b>{escape(current.current_price_text)}</b>")
    lines.append(f"Скидка: <b>{current.discount_percent}%</b>")
    if current.original_price_text:
        lines.append(f"Обычная цена: {escape(current.original_price_text)}")
    if changes:
        lines.append("")
        lines.append("Что изменилось:")
        for change in changes:
            lines.append(f"• {escape(change)}")
    lines.append("")
    lines.append(f"<a href=\"{escape(current.store_url)}\">Открыть страницу в Steam</a>")
    lines.append(f"Проверено: {escape(current.fetched_at)}")
    return "\n".join(lines)


def build_error_message(bundle_name: str, store_url: str, error_text: str) -> str:
    return "\n".join(
        [
            f"<b>Ошибка проверки {escape(bundle_name)}</b>",
            escape(error_text),
            "",
            f"<a href=\"{escape(store_url)}\">Страница Steam</a>",
            f"Время: {escape(utc_now_iso())}",
        ]
    )


def update_error_state(state: dict[str, Any], error_text: str) -> dict[str, Any]:
    state["last_error"] = error_text
    state["last_error_at"] = utc_now_iso()
    return state


def clear_error_state(state: dict[str, Any]) -> dict[str, Any]:
    state.pop("last_error", None)
    state.pop("last_error_at", None)
    return state


def run(force_notify: bool, send_test_message: bool) -> int:
    load_dotenv(Path(".env"))

    bundle_id = env("STEAM_BUNDLE_ID", DEFAULT_BUNDLE_ID) or DEFAULT_BUNDLE_ID
    bundle_name = env("STEAM_BUNDLE_NAME", DEFAULT_BUNDLE_NAME) or DEFAULT_BUNDLE_NAME
    steam_cc = env("STEAM_CC", DEFAULT_STEAM_CC) or DEFAULT_STEAM_CC
    steam_lang = env("STEAM_LANG", DEFAULT_STEAM_LANG) or DEFAULT_STEAM_LANG
    timeout = int(env("REQUEST_TIMEOUT", str(DEFAULT_TIMEOUT)) or DEFAULT_TIMEOUT)
    state_path = Path(env("STATE_FILE", DEFAULT_STATE_FILE) or DEFAULT_STATE_FILE)
    min_discount_percent = int(
        env("MIN_DISCOUNT_PERCENT", str(DEFAULT_MIN_DISCOUNT_PERCENT))
        or DEFAULT_MIN_DISCOUNT_PERCENT
    )
    notify_on_any_change = parse_bool(env("NOTIFY_ON_ANY_CHANGE"), True)
    notify_on_first_run = parse_bool(env("NOTIFY_ON_FIRST_RUN"), False)
    notify_on_errors = parse_bool(env("NOTIFY_ON_ERRORS"), True)

    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    store_url = build_store_url(bundle_id, steam_cc, steam_lang)
    state = load_state(state_path)

    if send_test_message:
        send_telegram_message(
            token,
            chat_id,
            (
                "<b>Тест Telegram-бота</b>\n"
                f"Бот для <b>{escape(bundle_name)}</b> на связи.\n"
                f"<a href=\"{escape(store_url)}\">Страница Steam</a>"
            ),
            timeout,
        )
        print("Test Telegram message sent.")
        return 0

    try:
        html = fetch_html(store_url, timeout)
        current = parse_bundle_snapshot(bundle_id, bundle_name, store_url, html)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        already_reported = state.get("last_error") == error_text
        next_state = update_error_state(state, error_text)
        save_state(state_path, next_state)
        print(error_text, file=sys.stderr)
        if notify_on_errors and not already_reported:
            send_telegram_message(
                token,
                chat_id,
                build_error_message(bundle_name, store_url, error_text),
                timeout,
            )
        return 1

    previous = state.get("snapshot", {})
    first_run = not previous
    changes = changed_fields(previous, current)
    notify = should_notify(
        first_run=first_run,
        changes=changes,
        discount_percent=current.discount_percent,
        min_discount_percent=min_discount_percent,
        notify_on_any_change=notify_on_any_change,
        notify_on_first_run=notify_on_first_run,
        force_notify=force_notify,
    )

    if notify:
        message = build_message(
            current=current,
            changes=changes,
            min_discount_percent=min_discount_percent,
            first_run=first_run,
            force_notify=force_notify,
        )
        send_telegram_message(token, chat_id, message, timeout)
        print("Telegram notification sent.")
    else:
        print("No changes worth notifying.")

    next_state = clear_error_state(state)
    next_state["snapshot"] = asdict(current)
    next_state["last_checked_at"] = utc_now_iso()
    save_state(state_path, next_state)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track Steam discounts for Rusty Lake Bundle and notify Telegram."
    )
    parser.add_argument(
        "--force-notify",
        action="store_true",
        help="Always send a Telegram message with the current price snapshot.",
    )
    parser.add_argument(
        "--send-test-message",
        action="store_true",
        help="Send a simple Telegram test message and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return run(force_notify=args.force_notify, send_test_message=args.send_test_message)
    except Exception as exc:  # pragma: no cover
        print(f"Fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())