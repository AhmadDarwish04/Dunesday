"""Poll cinema Sites for Date watches and Movie watches and alert via Telegram or Discord.

Watches are declared in `watches.json`. Date watches select a Site, movie, Cinema and
fixed date or weekday, sending one Alert when Showtimes become Published or Bookable.
Movie watches discover every Published date and Cinema, alerting on Bookable Showtimes
whose identities the Watch has never alerted about.

Two sites are supported, and both share the same core signal: **no showtimes parsed means
"not published yet"**, because each site only publishes a rolling window of upcoming dates.

  scene — Scene Cinemas District 5. Showtimes come from an AJAX fragment:
          <movie-details-url>?business_day=DD-MM-YYYY&ajax=1
          A date beyond the published window returns an empty body.

  vox   — VOX Cinemas Egypt. Showtimes come from the full page:
          /showtimes?c=<cinema>&m=<movie>&d=YYYYMMDD
          A date beyond the published window still returns a full page, but with a
          "No showtimes could be found" notice and no showtime elements.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import partial
from html import escape
from pathlib import Path
from typing import Callable, Mapping, NamedTuple, Optional
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests


@dataclass(frozen=True)
class Cinema:
    url: str
    name: str


SCENE_CINEMAS = {
    "district-5": Cinema("https://district5.scenecinemas.com/movie-details/{movie}.html",
                   "Scene Cinemas — District 5"),
    "cairo-festival-city": Cinema("https://cfc.scenecinemas.com/movie-details/{movie}.html",
                            "Scene Cinemas — Cairo Festival City"),
}
SCENE_SHOWTIMES_URL = "{base}?business_day={date}&ajax=1"
VOX_SHOWTIMES_URL = "https://egy.voxcinemas.com/showtimes?c={cinema}&m={movie}&d={date}"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

_COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
# Scene's fragment is fetched as an XHR; VOX's page is fetched as a top-level navigation.
SCENE_HEADERS = {
    **_COMMON_HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
VOX_HEADERS = {
    **_COMMON_HEADERS,
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

DEFAULT_STATE_PATH = Path(__file__).parent / "state.json"
DEFAULT_WATCHES_PATH = Path(__file__).parent / "watches.json"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

log = logging.getLogger("showtime_notifier")


@dataclass(frozen=True)
class Config:
    """Channel credentials and run-wide defaults, from environment variables."""
    telegram_token: str
    telegram_chat_id: str
    default_timezone: str
    watches_path: Path
    discord_webhook_url: str


NOTIFY_MODES = ("published", "bookable")


@dataclass(frozen=True)
class Watch:
    """One thing we're watching: a movie at a cinema on a target date.

    `notify_on` picks the trigger: "published" fires as soon as any showtime appears for
    the target date, "bookable" holds off until at least one of them can actually be
    booked. They differ because both sites list showtimes before opening sales.
    """
    id: str
    site: str
    movie_slug: str
    timezone: str
    cinema_slug: str = ""
    base_url: str = ""
    target_weekday: str = ""
    target_date: Optional[date] = None
    notify_on: str = "published"
    channels: list[str] = field(default_factory=lambda: ["telegram", "discord"])
    mention: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MovieWatch:
    """A movie across selected Cinemas and every Published date on its Sites."""
    id: str
    movie: dict[str, list[str]]
    timezone: str
    cinemas: str | list[str] = "all"
    until: Optional[date] = None
    channels: list[str] = field(default_factory=lambda: ["telegram", "discord"])
    mention: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Showtime:
    """A single showtime entry parsed from the page."""
    time: str
    href: str
    soldout: bool = False
    identity: str = ""
    cinema: str = ""
    experience: str = ""


@dataclass(frozen=True)
class Alert:
    """Text keyed by Channel name, with the mentions to include in the Alert."""
    texts: dict[str, str]
    mentions: list[str]


@dataclass(frozen=True)
class MoviePages:
    """One discovery page and its date pages, possibly covering all Cinemas."""
    build_url: Callable[[Optional[date]], str]
    parse: Callable[[str, date], list[Showtime]]


@dataclass(frozen=True)
class Site:
    """Per-site adapter: how to build the URL, fetch it, and read showtimes out of it."""
    build_url: Callable[[Watch, date], str]
    parse: Callable[[str], "dict[str, list[Showtime]]"]
    display_names: Callable[[str, Watch], "tuple[str, str]"]
    headers: dict
    # Consulted only when nothing parsed: is an empty result the site's normal
    # "nothing scheduled" response, or a sign the markup changed under us?
    empty_is_expected: Callable[[str], bool]
    # What a non-bookable time means on this site — Scene flags genuine sell-outs,
    # VOX uses one "unavailable" state for both sold out and not-yet-on-sale.
    soldout_label: str
    default_cinema_slug: str = ""
    require_cinema: bool = False
    cinemas: dict[str, Cinema] = field(default_factory=dict)
    movie_sources: Optional[Callable[[str, str | list[str]], list[MoviePages]]] = None
    movie_dates: Optional[Callable[[str], list[date]]] = None
    movie_title: Optional[Callable[[str, str], str]] = None
    discovery_empty_is_expected: Callable[[str], bool] = lambda html: False
    discovery_date: Callable[[str], Optional[date]] = lambda html: None
    # Known Cinema slugs and their display names; one mechanism for every Site.
    cinema_names: dict[str, str] = field(default_factory=dict)

    @property
    def default_cinema(self) -> str:
        return self.cinema_names.get(self.default_cinema_slug, "")

    def date_watch_settings(self, cinema: str, base_url: str, movie: str,
                            where: str, name: str) -> tuple[str, str]:
        if self.require_cinema and not cinema:
            raise RuntimeError(f"{where}: site {name!r} requires 'cinema_slug'")
        selected = cinema or self.default_cinema_slug
        if self.cinemas:
            if selected not in self.cinemas:
                raise RuntimeError(f"{where}: unknown {name} cinema_slug {cinema!r}; "
                                   f"valid slugs: {', '.join(self.cinemas)}")
            base_url = base_url or self.cinemas[selected].url
            if "{movie}" in base_url:
                base_url = base_url.format(movie=movie)
        return cinema, base_url

    def cinema_slug(self, name: str) -> Optional[str]:
        """Resolve a parsed Cinema name to one of this Site's known slugs."""
        normalised = re.sub(r"[\W_]+", "", name.casefold())
        return next((slug for slug, display in self.cinema_names.items()
                     if re.sub(r"[\W_]+", "", display.casefold()) == normalised), None)


def load_config(watches: list[Watch], env: Mapping[str, str]) -> Config:
    """Validate credentials only for Channels used by the supplied Watches."""
    used = {channel for watch in watches for channel in watch.channels}
    required = [key for channel in CHANNELS if channel in used
                for key in CHANNELS[channel].credentials]
    missing = [key for key in required if not env.get(key, "").strip()]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    watches_path, timezone = watch_defaults(env)
    return Config(
        telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
        default_timezone=timezone,
        watches_path=watches_path,
        discord_webhook_url=env.get("DISCORD_WEBHOOK_URL", ""),
    )


def parse_channels_and_mentions(e: dict, where: str) -> "tuple[list[str], list[str]]":
    """Validate the Channel list and Discord Mentions of a Watch entry."""
    channels = e.get("channels", ["telegram", "discord"])
    if not isinstance(channels, list) or not channels:
        raise RuntimeError(f"{where}: channels must be a non-empty list")
    for channel in channels:
        if not isinstance(channel, str) or channel not in CHANNELS:
            raise RuntimeError(f"{where}: unknown channel {channel!r}; expected telegram or discord")
    if len(set(channels)) != len(channels):
        raise RuntimeError(f"{where}: duplicate channel in channels")
    mention = e.get("mention", [])
    if not isinstance(mention, list):
        raise RuntimeError(f"{where}: mention must be a list of Discord user ID strings")
    if any(not isinstance(uid, str) or not re.fullmatch(r"[0-9]+", uid) for uid in mention):
        raise RuntimeError(f"{where}: mention must contain numeric Discord user ID strings")
    return channels, mention


def watch_timezone(entry: dict, default: str, where: str) -> str:
    tz = str(entry.get("timezone") or "").strip() or default
    try:
        ZoneInfo(tz)
    except Exception as exc:
        raise RuntimeError(f"{where}: unknown timezone {tz!r}") from exc
    return tz


def watch_defaults(env: Mapping[str, str]) -> tuple[Path, str]:
    return (Path(env.get("WATCHES_FILE") or DEFAULT_WATCHES_PATH),
            (env.get("TIMEZONE") or "Africa/Cairo").strip())


def load_watches(path: Path, default_timezone: str = "Africa/Cairo") -> "list[Watch | MovieWatch]":
    """Parse and validate watches.json into Watch objects.

    Each entry needs an `id`, a `site` we have an adapter for, a `movie_slug`, and a
    `target` of either {"weekday": "friday"} or {"date": "YYYY-MM-DD"} — not both.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("watches") if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not entries:
        raise RuntimeError(f"{path}: expected a non-empty 'watches' list")

    watches: list[Watch | MovieWatch] = []
    seen: set[str] = set()
    for i, e in enumerate(entries):
        where = f"{path} watch #{i}"
        wid = str(e.get("id") or "").strip()
        if not wid:
            raise RuntimeError(f"{where}: missing 'id'")
        if wid in seen:
            raise RuntimeError(f"{where}: duplicate id {wid!r}")
        seen.add(wid)

        if "movie" in e:
            if any(key in e for key in ("site", "movie_slug", "cinema_slug", "target", "base_url", "notify_on")):
                raise RuntimeError(f"{where}: cannot mix Date watch and Movie watch shapes")
            movie = e["movie"]
            if not isinstance(movie, dict) or not movie:
                raise RuntimeError(f"{where}: movie must be a per-Site slug map")
            for name, slugs in movie.items():
                if name not in SITES:
                    raise RuntimeError(f"{where}: unknown Site {name!r}")
                if not isinstance(slugs, list) or any(not isinstance(slug, str) or not slug.strip() for slug in slugs):
                    raise RuntimeError(f"{where}: {name} Movie slugs must be a list of non-empty strings")
            if not any(movie.values()):
                raise RuntimeError(f"{where}: Movie watch needs at least one Movie slug")
            cinemas = e.get("cinemas")
            if cinemas != "all" and (not isinstance(cinemas, list) or not cinemas
                                     or any(not isinstance(slug, str) for slug in cinemas)):
                raise RuntimeError(f"{where}: cinemas must be 'all' or a non-empty list of Cinema slugs")
            if isinstance(cinemas, list):
                known = {name: SITES[name].cinema_names for name, slugs in movie.items() if slugs}
                for slug in cinemas:
                    if not any(slug in names for names in known.values()):
                        valid = "; ".join(f"{name}: {', '.join(names)}" for name, names in known.items())
                        raise RuntimeError(f"{where}: unknown Cinema slug {slug!r}; valid slugs by Site: {valid}")
            tz = watch_timezone(e, default_timezone, where)
            until = None
            if "until" in e:
                try:
                    until = date.fromisoformat(e["until"].strip())
                except (ValueError, AttributeError) as exc:
                    raise RuntimeError(f"{where}: until must be YYYY-MM-DD, got {e['until']!r}") from exc
            channels, mention = parse_channels_and_mentions(e, where)
            watches.append(MovieWatch(id=wid, movie=movie, timezone=tz, cinemas=cinemas, until=until,
                                      channels=channels, mention=mention))
            continue
        if "cinemas" in e or "until" in e:
            raise RuntimeError(f"{where}: cannot mix Date watch and Movie watch shapes")

        site = str(e.get("site") or "").strip().lower()
        if site not in SITES:
            raise RuntimeError(f"{where}: site must be one of {sorted(SITES)}, got {site!r}")

        movie_slug = str(e.get("movie_slug") or "").strip()
        if not movie_slug:
            raise RuntimeError(f"{where}: missing 'movie_slug'")

        cinema_slug, base_url = SITES[site].date_watch_settings(
            str(e.get("cinema_slug") or "").strip(),
            str(e.get("base_url") or "").strip(), movie_slug, where, site)

        target = e.get("target") or {}
        weekday = str(target.get("weekday") or "").strip().lower()
        fixed_raw = str(target.get("date") or "").strip()
        if bool(weekday) == bool(fixed_raw):
            raise RuntimeError(f"{where}: target needs exactly one of 'weekday' or 'date'")
        if weekday and weekday not in WEEKDAYS:
            raise RuntimeError(f"{where}: weekday must be one of {WEEKDAYS}, got {weekday!r}")
        fixed = None
        if fixed_raw:
            try:
                fixed = date.fromisoformat(fixed_raw)
            except ValueError as exc:
                raise RuntimeError(f"{where}: date must be YYYY-MM-DD, got {fixed_raw!r}") from exc

        notify_on = str(e.get("notify_on") or "published").strip().lower()
        if notify_on not in NOTIFY_MODES:
            raise RuntimeError(
                f"{where}: notify_on must be one of {list(NOTIFY_MODES)}, got {notify_on!r}")

        channels, mention = parse_channels_and_mentions(e, where)
        tz = watch_timezone(e, default_timezone, where)

        watches.append(Watch(
            id=wid,
            site=site,
            movie_slug=movie_slug,
            timezone=tz,
            cinema_slug=cinema_slug,
            base_url=base_url,
            target_weekday=weekday,
            target_date=fixed,
            notify_on=notify_on,
            channels=channels,
            mention=mention,
        ))
    return watches


def next_target_date(weekday: str, tz: str, now: Optional[datetime] = None) -> date:
    """Return the next occurrence of `weekday` in `tz`; today if today already matches."""
    if now is None:
        now = datetime.now(ZoneInfo(tz))
    target_idx = WEEKDAYS.index(weekday.lower())
    today_idx = now.weekday()
    delta = (target_idx - today_idx) % 7
    return (now.date() + timedelta(days=delta))


def resolve_target_date(watch: Watch, now: Optional[datetime] = None) -> date:
    """The calendar date this watch is currently asking about."""
    if watch.target_date is not None:
        return watch.target_date
    return next_target_date(watch.target_weekday, watch.timezone, now=now)


def today_in(tz: str, now: Optional[datetime] = None) -> date:
    return (now or datetime.now(ZoneInfo(tz))).date()


def fetch_page(url: str, headers: Optional[dict] = None,
               attempts: int = 3, timeout: int = 20) -> str:
    """GET `url` impersonating Chrome's TLS fingerprint; retry on network errors and 5xx.

    Both sites' WAFs reject plain `requests`/`urllib3` TLS handshakes, so we use curl_cffi.
    """
    last_err: Optional[Exception] = None
    for i in range(attempts):
        try:
            r = cffi_requests.get(
                url, headers=headers or SCENE_HEADERS, timeout=timeout, impersonate="chrome124"
            )
            if 500 <= r.status_code < 600:
                raise RuntimeError(f"server error {r.status_code}")
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r.text
        except Exception as e:  # noqa: BLE001 — curl_cffi raises its own errors
            last_err = e
            backoff = 2 ** i
            log.warning("fetch attempt %d/%d failed: %s (sleeping %ds)", i + 1, attempts, e, backoff)
            if i < attempts - 1:
                time.sleep(backoff)
    raise RuntimeError(f"failed to fetch {url} after {attempts} attempts: {last_err}")


# --------------------------------------------------------------------------- scene


def scene_url(watch: Watch, target: date) -> str:
    return SCENE_SHOWTIMES_URL.format(base=watch.base_url, date=target.strftime("%d-%m-%Y"))


def parse_showtimes_scene(html: str) -> "dict[str, list[Showtime]]":
    """Parse the Scene Cinemas AJAX fragment, returning {screen_type: [Showtime, ...]}.

    Showtimes are grouped by an experience label span (e.g. `IMAX`, `Premiere`,
    `Standard & Deluxe`) whose class starts with `ex_`. Sold-out entries carry the
    `showtime_soldout` class and a `javascript:void(0)` href. Returns an empty dict when
    the fragment is empty (no showtimes scheduled yet for that date).
    """
    text = html.strip()
    if not text:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    groups: dict[str, list[Showtime]] = {}

    # Each experience label is a <span class="ex_imax|ex_vip|ex_stand|...">. The content
    # wrapper divs are `ex_*_content`, so exclude those to keep only label spans.
    label_spans = soup.find_all(
        "span", class_=lambda c: bool(c) and any(
            cls.startswith("ex_") and not cls.endswith("_content") for cls in c.split()
        )
    )
    for span in label_spans:
        label = span.get_text(strip=True)
        if not label:
            continue
        container = span.find_parent("div")
        if container is None:
            continue
        for a in container.select("ul li a"):
            time_text = a.get_text(strip=True)
            if not time_text:
                continue
            classes = a.get("class") or []
            href = (a.get("href") or "").strip()
            soldout = "showtime_soldout" in classes or href.lower().startswith("javascript:")
            groups.setdefault(label, []).append(
                Showtime(time=time_text, href=href, soldout=soldout)
            )
    return groups


def scene_display_names(html: str, watch: Watch) -> "tuple[str, str]":
    """Return (movie_title, cinema_name); cinema comes from the fragment's branch label,
    falling back to the selected Scene Cinema's table name."""
    movie = title_from_slug(watch.movie_slug)

    cinema = SITES["scene"].cinema_names[watch.cinema_slug or SITES["scene"].default_cinema_slug]
    soup = BeautifulSoup(html, "html.parser")
    branch = soup.find(class_="branch")
    if branch and branch.get_text(strip=True):
        cinema = re.sub(r"\s+", " ", branch.get_text(strip=True)).strip()

    return movie, cinema


def scene_empty_is_expected(html: str) -> bool:
    """An empty fragment IS Scene's 'nothing scheduled' response, so nothing looks wrong."""
    return True


def scene_movie_url(slug: str, target: Optional[date] = None, *, cinema: str) -> str:
    base = SCENE_CINEMAS[cinema].url.format(movie=slug)
    return SCENE_SHOWTIMES_URL.format(base=base, date=target.strftime("%d-%m-%Y")) if target else base


def scene_movie_dates(html: str) -> list[date]:
    soup = BeautifulSoup(html, "html.parser")
    return sorted({datetime.strptime(day["id"].removeprefix("data-"), "%d-%m-%Y").date()
                   for day in soup.select("li.calanderdays[id]")})


def scene_movie_title(html: str, slug: str) -> str:
    heading = BeautifulSoup(html, "html.parser").select_one(".movie-name")
    return heading.get_text(" ", strip=True) if heading else title_from_slug(slug)


def parse_movie_showtimes_scene(html: str, target: date, *, cinema: str, movie_url: str) -> list[Showtime]:
    # Scene has no stable per-Showtime id. Movie variants share the same identity
    # when Cinema, date, time and Experience match; booking URLs may change.
    return [Showtime(time=st.time, href=movie_url, soldout=st.soldout,
                     identity=f"scene:{cinema}:{target:%Y%m%d}:{st.time}:{experience}",
                     cinema=SCENE_CINEMAS[cinema].name, experience=experience)
            for experience, times in parse_showtimes_scene(html).items() for st in times]


# ----------------------------------------------------------------------------- vox


def vox_url(watch: Watch, target: date) -> str:
    return VOX_SHOWTIMES_URL.format(
        cinema=watch.cinema_slug, movie=watch.movie_slug, date=target.strftime("%Y%m%d")
    )


def parse_showtimes_vox(html: str) -> "dict[str, list[Showtime]]":
    """Parse the VOX showtimes page, returning {screen_type: [Showtime, ...]}.

    Bookable times are `<a class="action showtime" href="/booking/...">`; times that exist
    but can't be booked (sold out or not yet on sale) are `<span class="action showtime
    unavailable">` with no href. Both count as "showtimes are published" — only the
    booking link differs — so we keep both and flag the spans as sold out.

    Screen type comes from the `<strong>` label of the enclosing `<li>` in `ol.showtimes`.
    """
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select("a.action.showtime, span.action.showtime")
    if not nodes:
        return {}

    groups: dict[str, list[Showtime]] = {}
    for node in nodes:
        text = node.get_text(strip=True)
        if not text:
            continue
        classes = node.get("class") or []
        href = (node.get("href") or "").strip()
        soldout = node.name != "a" or "unavailable" in classes or not href
        groups.setdefault(_vox_group_label(node), []).append(
            Showtime(time=text, href=href, soldout=soldout)
        )
    return groups


def _vox_group_label(node) -> str:
    """Walk up from a showtime node to the nearest <li> carrying a <strong> screen label."""
    for parent in node.parents:
        if parent.name == "li":
            strong = parent.find("strong", recursive=False)
            if strong and strong.get_text(strip=True):
                return strong.get_text(strip=True)
    return "Showtimes"


def vox_display_names(html: str, watch: Watch) -> "tuple[str, str]":
    """Movie from the showtimes article's <h2>, cinema from the `.highlight` heading.

    The lookup is scoped to `article.movie-compare` on purpose: the page footer also has
    <h2> elements ("Stay in touch"), which a bare `find("h2")` would happily return.
    """
    soup = BeautifulSoup(html, "html.parser")

    movie = title_from_slug(watch.movie_slug)
    heading = soup.select_one("article.movie-compare h2") or soup.select_one("section.showtimes h2")
    if heading and heading.get_text(strip=True):
        movie = re.sub(r"\s+", " ", heading.get_text(strip=True)).strip()

    cinema = title_from_slug(watch.cinema_slug)
    branch = soup.select_one(".dates h3.highlight") or soup.select_one("h3.highlight")
    if branch and branch.get_text(strip=True):
        cinema = re.sub(r"\s+", " ", branch.get_text(strip=True)).strip()

    return movie, cinema


def vox_empty_is_expected(html: str) -> bool:
    """VOX states 'no showtimes' explicitly; its absence means the page structure moved."""
    return "no showtimes could be found" in html.lower()


def vox_movie_dates(html: str) -> list[date]:
    """Read the bare current date and linked dates from VOX's date picker."""
    soup = BeautifulSoup(html, "html.parser")
    values = []
    current = soup.select_one('section.showtimes input[name=d]')
    if current and current.get("value"):
        values.append(current["value"])
    for anchor in soup.select("nav.date-filter a[href]"):
        values.extend(parse_qs(urlparse(anchor["href"]).query).get("d", []))
    return sorted({datetime.strptime(value, "%Y%m%d").date() for value in values})


def vox_discovery_date(html: str) -> Optional[date]:
    current = BeautifulSoup(html, "html.parser").select_one('section.showtimes input[name=d]')
    return datetime.strptime(current["value"], "%Y%m%d").date() if current and current.get("value") else None


def vox_movie_url(slug: str, target: Optional[date] = None) -> str:
    url = f"https://egy.voxcinemas.com/showtimes?m={slug}"
    return f"{url}&d={target:%Y%m%d}" if target else url


def vox_movie_title(html: str, slug: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("article.movie-compare h2")
    return heading.get_text(" ", strip=True) if heading else title_from_slug(slug)


def parse_movie_showtimes_vox(html: str, target: date) -> list[Showtime]:
    """Read Bookable and Not bookable Showtimes with Cinema and stable VOX identity."""
    soup = BeautifulSoup(html, "html.parser")
    showtimes = []
    for heading in soup.select("h3.highlight"):
        cinema = heading.get_text(" ", strip=True)
        block = heading.find_next_sibling("ol", class_="showtimes")
        if block is None:
            continue
        for node in block.select("a.action.showtime, span.action.showtime"):
            identity = node.find_parent("li").get("data-id")
            if not identity:
                raise ValueError("VOX Showtime is missing li[data-id]")
            href = node.get("href", "")
            soldout = node.name != "a" or "unavailable" in node.get("class", []) or not href
            showtimes.append(Showtime(
                time=node.get_text(strip=True), href=urljoin("https://egy.voxcinemas.com", href),
                soldout=soldout, identity=identity, cinema=cinema,
                experience=_vox_group_label(node)))
    return showtimes


def scene_movie_sources(slug: str, cinemas: str | list[str]) -> list[MoviePages]:
    return [MoviePages(
        build_url=partial(scene_movie_url, slug, cinema=cinema),
        parse=partial(parse_movie_showtimes_scene, cinema=cinema,
                      movie_url=scene_movie_url(slug, cinema=cinema)))
        for cinema in SCENE_CINEMAS if cinemas == "all" or cinema in cinemas]


def vox_movie_sources(slug: str, cinemas: str | list[str]) -> list[MoviePages]:
    return [MoviePages(build_url=partial(vox_movie_url, slug), parse=parse_movie_showtimes_vox)]


SITES: "dict[str, Site]" = {
    "scene": Site(
        build_url=scene_url,
        parse=parse_showtimes_scene,
        display_names=scene_display_names,
        headers=SCENE_HEADERS,
        empty_is_expected=scene_empty_is_expected,
        soldout_label="sold out",
        default_cinema_slug="district-5",
        cinemas=SCENE_CINEMAS,
        movie_sources=scene_movie_sources,
        movie_dates=scene_movie_dates,
        discovery_empty_is_expected=lambda html: not html.strip() or "no showtimes" in html.lower(),
        movie_title=scene_movie_title,
        cinema_names={slug: details.name for slug, details in SCENE_CINEMAS.items()},
    ),
    "vox": Site(
        build_url=vox_url,
        parse=parse_showtimes_vox,
        display_names=vox_display_names,
        headers=VOX_HEADERS,
        empty_is_expected=vox_empty_is_expected,
        # VOX shows one "unavailable" state for both sold out and not-yet-on-sale, and
        # times routinely flip from unavailable to bookable — so don't claim "sold out".
        soldout_label="not bookable",
        require_cinema=True,
        movie_sources=vox_movie_sources,
        movie_dates=vox_movie_dates,
        discovery_date=vox_discovery_date,
        discovery_empty_is_expected=vox_empty_is_expected,
        movie_title=vox_movie_title,
        cinema_names={
            "city-centre-alexandria": "City Centre Alexandria",
            "city-centre-almaza": "City Centre Almaza",
            "mall-of-egypt": "Mall of Egypt",
        },
    ),
}


# --------------------------------------------------------------------------- output


def title_from_slug(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-"))


def should_notify(notify_on: str, total: int, bookable: int) -> bool:
    """Has this watch's trigger fired? Split out from `run_watch` so it's directly testable."""
    if total == 0:
        return False
    if notify_on == "bookable":
        return bookable > 0
    return True


def format_message(movie: str, cinema: str, target_date: date,
                   groups: "dict[str, list[Showtime]]",
                   soldout_label: str = "sold out",
                   headline: str = "showtimes are live!") -> str:
    """Format the HTML-mode Telegram message body."""
    date_str = f"{target_date:%A, %B} {target_date.day}, {target_date:%Y}"
    lines = [
        f"🎬 <b>{movie}</b> {headline}",
        f"📍 {cinema}",
        f"📅 {date_str}",
    ]
    for screen_type, times in groups.items():
        lines.append("")
        lines.append(f"<b>{screen_type}</b>")
        for st in times:
            if st.soldout:
                lines.append(f"• <s>{st.time}</s> ({soldout_label})")
            elif st.href:
                lines.append(f"• <a href=\"{st.href}\">{st.time}</a>")
            else:
                lines.append(f"• {st.time}")
    return "\n".join(lines)


def escape_discord(text: str) -> str:
    return re.sub(r"([\\`*_\[\]<>|~])", r"\\\1", text)


def format_discord_message(movie: str, cinema: str, target_date: date,
                           groups: "dict[str, list[Showtime]]",
                           soldout_label: str = "sold out",
                           headline: str = "showtimes are live!") -> str:
    """Format the same Date watch in Discord markdown."""
    date_str = f"{target_date:%A, %B} {target_date.day}, {target_date:%Y}"
    lines = [f"\U0001f3ac **{escape_discord(movie)}** {headline}", f"\U0001f4cd {escape_discord(cinema)}", f"\U0001f4c5 {date_str}"]
    for experience, times in groups.items():
        lines.extend(["", f"**{escape_discord(experience)}**"])
        for st in times:
            if st.soldout:
                lines.append(f"\u2022 ~~{st.time}~~ ({soldout_label})")
            elif st.href:
                lines.append(f"\u2022 [{st.time}]({st.href})")
            else:
                lines.append(f"\u2022 {st.time}")
    return "\n".join(lines)


def build_alert(watch: Watch, movie: str, cinema: str, target: date,
                groups: "dict[str, list[Showtime]]", soldout_label: str,
                headline: str) -> Alert:
    """Render only the Watch's chosen Channels, including Discord Mentions."""
    texts = {channel: CHANNELS[channel].mention_prefix(watch.mention) +
             CHANNELS[channel].format_date(movie, cinema, target, groups, soldout_label, headline)
             for channel in watch.channels}
    return Alert(texts=texts, mentions=list(watch.mention))


def split_text(text: str, limit: int, hard_split: bool = False) -> list[str]:
    """Keep lines intact; hard splitting is safe only for plain-text Channels."""
    chunks = []
    current = ""
    for line in text.splitlines(keepends=hard_split):
        if not hard_split and len(line) > limit:
            raise ValueError(f"Telegram Alert line exceeds {limit} characters")
        joined = current + ("" if hard_split else "\n") + line if current else line
        if len(joined) > limit:
            if current:
                chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = current + ("" if hard_split else "\n") + line if current else line
    if current:
        chunks.append(current)
    return chunks


def split_discord_text(text: str) -> list[str]:
    return split_text(text, 2000, hard_split=True)


def telegram_messages(text: str) -> list[str]:
    return split_text(text, 4096)


def send_telegram(token: str, chat_id: str, text: str) -> None:
    """POST a message to the Telegram Bot API; raise on failure."""
    r = requests.post(
        TELEGRAM_URL.format(token=token),
        json={
            "chat_id": chat_id,
            "parse_mode": "HTML",
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram API error {r.status_code}: {r.text}")
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API returned not-ok: {body}")


def send_discord(webhook_url: str, text: str, mentions: list[str]) -> None:
    """POST each part to Discord, permitting only the Watch's listed user Mentions."""
    for content in split_discord_text(text):
        r = requests.post(
            webhook_url,
            json={"content": content, "allowed_mentions": {"parse": [], "users": mentions}},
            timeout=20,
        )
        if not r.ok:
            raise RuntimeError(f"Discord API error {r.status_code}: {r.text}")


def discord_mention_prefix(mentions: list[str]) -> str:
    return " ".join(f"<@{uid}>" for uid in mentions) + "\n" if mentions else ""


def deliver_telegram(cfg: Config, text: str, mentions: list[str]) -> None:
    for part in telegram_messages(text):
        send_telegram(cfg.telegram_token, cfg.telegram_chat_id, part)


@dataclass(frozen=True)
class Channel:
    format_date: Callable
    escape: Callable[[str], str]
    bold_template: str
    link_template: str
    escape_url: Callable[[str], str]
    send: Callable[[Config, str, list[str]], None]
    credentials: tuple[str, ...]
    mention_prefix: Callable[[list[str]], str] = lambda mentions: ""

    def bold(self, text: str) -> str:
        return self.bold_template.format(self.escape(text))

    def link(self, st: Showtime) -> str:
        return self.link_template.format(time=self.escape(st.time), href=self.escape_url(st.href))


CHANNELS = {
    "telegram": Channel(format_message, escape, "<b>{}</b>", '<a href="{href}">{time}</a>', escape,
                        deliver_telegram, ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")),
    "discord": Channel(format_discord_message, escape_discord, "**{}**", "[{time}]({href})", lambda url: url,
                       lambda cfg, text, mentions: send_discord(cfg.discord_webhook_url, text, mentions),
                       ("DISCORD_WEBHOOK_URL",), discord_mention_prefix),
}


def send_alert(cfg: Config, alert: Alert) -> None:
    """Attempt every Channel; any failure prevents this Watch's state being committed."""
    failures = []
    for channel, text in alert.texts.items():
        try:
            CHANNELS[channel].send(cfg, text, alert.mentions)
        except Exception as exc:  # noqa: BLE001 — attempt the other Channels too
            failures.append(f"{channel}: {exc}")
    if failures:
        raise RuntimeError("Channel delivery failed: " + "; ".join(failures))


# ---------------------------------------------------------------------------- state


def load_state(path: Path, watches: "Optional[list[Watch | MovieWatch]]" = None) -> dict:
    """Read Date watch dedupe keys and optional Movie watch alerted identities.

    `movie_alerted` maps Watch id to {Showtime identity: "YYYYMMDD"}.

    Older runs stored a single bare string for the one watch that existed then; that value
    is migrated onto the first watch so an already-sent notification isn't repeated.
    """
    if not path.exists():
        return {"notified_for": {}}

    state = json.loads(path.read_text(encoding="utf-8"))
    movie_state = {"movie_alerted": state["movie_alerted"]} if "movie_alerted" in state else {}
    notified = state.get("notified_for")
    if isinstance(notified, dict):
        return {**movie_state, "notified_for": notified}
    if isinstance(notified, str) and watches:
        return {**movie_state, "notified_for": {watches[0].id: notified}}
    return {**movie_state, "notified_for": {}}


def save_state(path: Path, state: dict) -> None:
    """Persist state.json with a trailing newline."""
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------ run


def format_movie_message(channel: str, movie: str, groups: dict, count: int) -> str:
    """Render New Showtimes grouped Cinema -> date -> Experience for one Channel."""
    adapter = CHANNELS[channel]
    name = adapter.bold(movie)
    lines = [f"\U0001f3ac {name} is now bookable! {count} New Showtime{'' if count == 1 else 's'}"]
    for cinema, dates in groups.items():
        lines.extend(["", f"\U0001f4cd {adapter.escape(cinema)}"])
        for target, experiences in sorted(dates.items()):
            lines.append(f"\U0001f4c5 {target:%A, %B} {target.day}, {target:%Y}")
            for experience, times in experiences.items():
                lines.extend(["", adapter.bold(experience)])
                for st in times:
                    lines.append("\u2022 " + adapter.link(st))
    return "\n".join(lines)


class WatchResult(NamedTuple):
    alerts: list[Alert]
    state: dict
    failures: tuple[str, ...] = ()


def warn_markup(watch_id: str, url: str, detail: str = "no showtimes AND no expected empty-state marker") -> None:
    log.warning("[%s] %s - the page structure may have changed (%s)", watch_id, detail, url)


def discover_dates(watch: MovieWatch, site: Site, source: MoviePages, fetch) -> tuple[str, list[date]]:
    url = source.build_url(None)
    html = fetch(url, site.headers)
    targets = site.movie_dates(html)
    if not targets and not site.discovery_empty_is_expected(html):
        warn_markup(watch.id, url)
    return html, targets


def fetch_showtimes(watch: MovieWatch, site: Site, source: MoviePages, fetch,
                    discovery: str, target: date) -> list[Showtime]:
    url = source.build_url(target)
    html = discovery if target == site.discovery_date(discovery) else fetch(url, site.headers)
    showtimes = source.parse(html, target)
    if not showtimes and not site.empty_is_expected(html):
        warn_markup(watch.id, url)
    return showtimes


def filter_cinemas(watch: MovieWatch, site: Site, showtimes: list[Showtime], url: str) -> list[Showtime]:
    selected = []
    for cinema in dict.fromkeys(st.cinema for st in showtimes):
        slug = site.cinema_slug(cinema)
        if slug is None:
            warn_markup(watch.id, url, f"unknown Cinema {cinema!r}")
        if watch.cinemas == "all" or slug in watch.cinemas:
            selected.extend(st for st in showtimes if st.cinema == cinema)
    return selected


def new_showtimes(showtimes: list[Showtime], alerted: dict, target: date) -> list[Showtime]:
    new = []
    for st in showtimes:
        if not st.soldout and st.identity not in alerted:
            alerted[st.identity] = target.strftime("%Y%m%d")
            new.append(st)
    return new


class PacedFetch:
    """Share the pause between page fetches across every Watch in a run."""
    def __init__(self, fetch, sleep=None):
        self.fetch = fetch
        self.sleep = sleep
        self.fetched = False

    def __call__(self, url, headers):
        if self.fetched and self.sleep is not None:
            self.sleep(1)
        self.fetched = True
        return self.fetch(url, headers)


def run_movie_watch(watch: MovieWatch, fetch: Callable[[str, dict], str], state: dict,
                    now: datetime) -> WatchResult:
    """Collect New Showtimes through Site adapters, retaining successful pages on failure."""
    alerted = {identity: target for identity, target in
               state.get("movie_alerted", {}).get(watch.id, {}).items()
               if target >= now.strftime("%Y%m%d")}
    groups = {}
    movie = ""
    failures = []
    for name, slugs in watch.movie.items():
        site = SITES[name]
        if watch.cinemas != "all" and not any(c in site.cinema_names for c in watch.cinemas):
            continue
        for slug in slugs:
            for source in site.movie_sources(slug, watch.cinemas):
                url = source.build_url(None)
                try:
                    discovery, targets = discover_dates(watch, site, source, fetch)
                    if not movie:
                        movie = site.movie_title(discovery, slug)
                except Exception as exc:
                    log.error("[%s] failed %s: %s", watch.id, url, exc)
                    failures.append(f"{url}: {exc}")
                    continue
                for target in targets:
                    if target < now.date():
                        continue
                    date_url = source.build_url(target)
                    try:
                        showtimes = fetch_showtimes(watch, site, source, fetch, discovery, target)
                        selected = filter_cinemas(watch, site, showtimes, date_url)
                        for st in new_showtimes(selected, alerted, target):
                            groups.setdefault(st.cinema, {}).setdefault(target, {}).setdefault(st.experience, []).append(st)
                    except Exception as exc:
                        log.error("[%s] failed %s: %s", watch.id, date_url, exc)
                        failures.append(f"{date_url}: {exc}")
    if not groups:
        return WatchResult([], state, tuple(failures))
    count = sum(len(times) for dates in groups.values()
                for experiences in dates.values() for times in experiences.values())
    texts = {channel: CHANNELS[channel].mention_prefix(watch.mention) +
             format_movie_message(channel, movie, groups, count) for channel in watch.channels}
    proposed = {**state, "movie_alerted": {**state.get("movie_alerted", {}), watch.id: alerted}}
    return WatchResult([Alert(texts=texts, mentions=list(watch.mention))], proposed, tuple(failures))


def run_watch(watch: Watch | MovieWatch, fetch: Callable[[str, dict], str], state: dict,
              now: datetime, sleep: Optional[Callable[[float], None]] = None) -> WatchResult:
    """Evaluate one Watch, returning Alerts and proposed state without delivery.

    The caller commits the proposed state only after all Alerts have been delivered.
    Failures accompany successful Movie pages in the result. No clock or sleep
    is used here unless the caller injects it.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    now = now.astimezone(ZoneInfo(watch.timezone))
    if not isinstance(fetch, PacedFetch):
        fetch = PacedFetch(fetch, sleep)
    if isinstance(watch, MovieWatch):
        if watch.until is not None and now.date() > watch.until:
            log.info("[%s] until date %s has passed - skipping", watch.id, watch.until.isoformat())
            return WatchResult([], state)
        return run_movie_watch(watch, fetch, state, now)
    site = SITES[watch.site]
    target = resolve_target_date(watch, now=now)
    dedupe_key = target.strftime("%Y%m%d")

    if watch.target_date is not None and target < today_in(watch.timezone, now=now):
        log.info("[%s] target date %s has passed — skipping", watch.id, target.isoformat())
        return WatchResult([], state)

    url = site.build_url(watch, target)
    log.info("[%s] checking %s on %s", watch.id, watch.movie_slug, target.isoformat())
    log.info("[%s] URL: %s", watch.id, url)

    html = fetch(url, site.headers)
    groups = site.parse(html)
    total = sum(len(v) for v in groups.values())

    if total == 0:
        if not site.empty_is_expected(html):
            warn_markup(watch.id, url)
        log.info("[%s] No showtimes yet for %s", watch.id, target.isoformat())
        return WatchResult([], state)

    bookable = sum(1 for v in groups.values() for s in v if not s.soldout)
    log.info("[%s] Found %d showtimes (%d bookable) across %d screen types: %s",
             watch.id, total, bookable, len(groups), ", ".join(groups.keys()))

    if not should_notify(watch.notify_on, total, bookable):
        # notify_on="bookable": the date is published but sales haven't opened. Stay
        # silent and keep checking — state is deliberately not written here.
        log.info("[%s] %d showtimes listed but none bookable yet — waiting", watch.id, total)
        return WatchResult([], state)

    if state["notified_for"].get(watch.id) == dedupe_key:
        log.info("[%s] Already notified for %s", watch.id, dedupe_key)
        return WatchResult([], state)

    movie, cinema = site.display_names(html, watch)
    headline = "is now bookable!" if watch.notify_on == "bookable" else "showtimes are live!"
    alert = build_alert(watch, movie, cinema, target, groups,
                        soldout_label=site.soldout_label, headline=headline)
    next_state = {**state, "notified_for": {**state["notified_for"], watch.id: dedupe_key}}
    return WatchResult([alert], next_state)


def main() -> int:
    """Check every configured watch, isolating failures so one bad site can't mask the rest."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    path, timezone = watch_defaults(os.environ)
    watches = load_watches(path, timezone)
    cfg = load_config(watches, os.environ)
    state = load_state(DEFAULT_STATE_PATH, watches)
    log.info("loaded %d watch(es): %s", len(watches), ", ".join(w.id for w in watches))

    changed = False
    failures: list[str] = []
    fetch = PacedFetch(fetch_page, time.sleep)
    for watch in watches:
        try:
            result = run_watch(watch, fetch, state, datetime.now(ZoneInfo(watch.timezone)))
            alerts, next_state, page_failures = result
            if page_failures:
                failures.append(watch.id)
            for alert in alerts:
                send_alert(cfg, alert)
                log.info("[%s] Alert sent to %s", watch.id, ", ".join(alert.texts))
            if alerts and next_state != state:
                state = next_state
                changed = True
        except Exception as e:  # noqa: BLE001 — one watch must not sink the others
            log.error("[%s] failed: %s", watch.id, e)
            if watch.id not in failures:
                failures.append(watch.id)

    if changed:
        save_state(DEFAULT_STATE_PATH, state)

    if failures:
        log.error("%d of %d watches failed: %s", len(failures), len(watches), ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log.error("fatal: %s", e)
        sys.exit(1)
