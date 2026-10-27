"""Unit tests for check_showtimes."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_showtimes import (  # noqa: E402
    SITES,
    Showtime,
    MovieWatch,
    Watch,
    format_message,
    load_config,
    load_state,
    load_watches,
    next_target_date,
    parse_showtimes_scene,
    parse_showtimes_vox,
    resolve_target_date,
    run_watch,
    scene_url,
    should_notify,
    vox_display_names,
    vox_empty_is_expected,
    vox_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------ target dates


def test_next_target_date_when_today_is_target():
    # 2026-05-22 is a Friday in Africa/Cairo.
    now = datetime(2026, 5, 22, 10, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    assert next_target_date("friday", "Africa/Cairo", now=now).isoformat() == "2026-05-22"


def test_next_target_date_when_target_is_future():
    # 2026-05-20 is a Wednesday → next Friday is 2026-05-22.
    now = datetime(2026, 5, 20, 10, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    assert next_target_date("friday", "Africa/Cairo", now=now).isoformat() == "2026-05-22"
    # Saturday from Wednesday → 2026-05-23.
    assert next_target_date("saturday", "Africa/Cairo", now=now).isoformat() == "2026-05-23"


def test_resolve_target_date_fixed_date_ignores_clock():
    watch = Watch(id="w", site="vox", movie_slug="m", timezone="Africa/Cairo",
                  cinema_slug="c", target_date=date(2026, 8, 6))
    now = datetime(2026, 7, 27, 10, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    assert resolve_target_date(watch, now=now) == date(2026, 8, 6)


def test_resolve_target_date_weekday_follows_clock():
    watch = Watch(id="w", site="scene", movie_slug="m", timezone="Africa/Cairo",
                  target_weekday="friday")
    now = datetime(2026, 7, 27, 10, 0, tzinfo=ZoneInfo("Africa/Cairo"))  # Monday
    assert resolve_target_date(watch, now=now) == date(2026, 7, 31)


# ------------------------------------------------------------------- scene parse


def test_parse_scene_no_showtimes_returns_empty():
    # An empty AJAX fragment means the date has no showtimes scheduled yet.
    assert parse_showtimes_scene(_read("scene_no_showtimes.html")) == {}
    assert parse_showtimes_scene("   \n  ") == {}


def test_parse_scene_groups_by_experience():
    groups = parse_showtimes_scene(_read("scene_with_showtimes.html"))
    assert list(groups.keys()) == ["IMAX", "Premiere", "Standard & Deluxe"]
    assert [s.time for s in groups["IMAX"]] == ["04:00 PM", "08:00 PM", "12:00 AM"]
    assert [s.time for s in groups["Standard & Deluxe"]] == ["04:00 PM"]


def test_parse_scene_marks_soldout_and_bookable():
    groups = parse_showtimes_scene(_read("scene_with_showtimes.html"))
    imax = {s.time: s for s in groups["IMAX"]}
    # 08:00 PM has a real booking link; the others are struck-through / void hrefs.
    assert imax["08:00 PM"].soldout is False
    assert imax["08:00 PM"].href.startswith("https://district5.scenecinemas.com/showtime-")
    assert imax["04:00 PM"].soldout is True
    assert imax["12:00 AM"].soldout is True
    # Premiere is entirely sold out.
    assert all(s.soldout for s in groups["Premiere"])
    # Standard & Deluxe 04:00 PM is bookable.
    assert groups["Standard & Deluxe"][0].soldout is False


# --------------------------------------------------------------------- vox parse


def test_parse_vox_no_showtimes_returns_empty():
    # VOX serves a full page with a notice rather than an empty body.
    html = _read("vox_no_showtimes.html")
    assert parse_showtimes_vox(html) == {}
    assert vox_empty_is_expected(html) is True


def test_vox_empty_is_expected_flags_unexpected_page():
    # No showtimes AND no notice → the page structure changed and we should warn.
    assert vox_empty_is_expected("<html><body><p>Something else entirely</p></body></html>") is False


def test_parse_vox_groups_by_screen_type():
    groups = parse_showtimes_vox(_read("vox_with_showtimes.html"))
    assert list(groups.keys()) == ["GOLD", "4DX", "Standard"]
    assert [s.time for s in groups["Standard"]] == ["1:45pm", "5:00pm", "8:15pm", "11:30pm"]


def test_parse_vox_treats_unavailable_spans_as_soldout():
    groups = parse_showtimes_vox(_read("vox_with_showtimes.html"))
    # GOLD/4DX are <span class="... unavailable"> — published but not bookable.
    assert all(s.soldout for s in groups["GOLD"])
    assert all(s.href == "" for s in groups["GOLD"])
    assert all(s.soldout for s in groups["4DX"])
    # Standard entries are real links.
    standard = groups["Standard"]
    assert all(not s.soldout for s in standard)
    assert standard[0].href == "https://egy.voxcinemas.com/booking/0047-266952"


def test_vox_display_names_from_page():
    watch = Watch(id="w", site="vox", movie_slug="spider-man-brand-new-day",
                  timezone="Africa/Cairo", cinema_slug="city-centre-almaza")
    movie, cinema = vox_display_names(_read("vox_with_showtimes.html"), watch)
    assert movie == "Spider-Man: Brand New Day"
    # Must be the cinema heading, not the "other movies you might like" h3.
    assert cinema == "City Centre Almaza"


def test_vox_display_names_ignore_footer_headings():
    """The footer's <h2>Stay in touch</h2> must never be mistaken for the movie title."""
    watch = Watch(id="w", site="vox", movie_slug="spider-man-brand-new-day",
                  timezone="Africa/Cairo", cinema_slug="city-centre-almaza")
    movie, _ = vox_display_names(_read("vox_no_showtimes.html"), watch)
    assert movie == "Spider Man Brand New Day"


def test_vox_display_names_fall_back_to_slugs():
    watch = Watch(id="w", site="vox", movie_slug="spider-man-brand-new-day",
                  timezone="Africa/Cairo", cinema_slug="city-centre-almaza")
    movie, cinema = vox_display_names("<html><body></body></html>", watch)
    assert movie == "Spider Man Brand New Day"
    assert cinema == "City Centre Almaza"


# ----------------------------------------------------------------------- url building


def test_url_builders_use_each_sites_date_format():
    scene = Watch(id="s", site="scene", movie_slug="the-odyssey", timezone="Africa/Cairo",
                  base_url="https://district5.scenecinemas.com/movie-details/the-odyssey.html",
                  target_weekday="friday")
    assert scene_url(scene, date(2026, 7, 31)) == (
        "https://district5.scenecinemas.com/movie-details/the-odyssey.html"
        "?business_day=31-07-2026&ajax=1"
    )

    vox = Watch(id="v", site="vox", movie_slug="spider-man-brand-new-day",
                timezone="Africa/Cairo", cinema_slug="city-centre-almaza",
                target_date=date(2026, 8, 6))
    assert vox_url(vox, date(2026, 8, 6)) == (
        "https://egy.voxcinemas.com/showtimes"
        "?c=city-centre-almaza&m=spider-man-brand-new-day&d=20260806"
    )


# -------------------------------------------------------------------------- watches


def _write_watches(tmp_path: Path, watches: list) -> Path:
    p = tmp_path / "watches.json"
    p.write_text(json.dumps({"watches": watches}), encoding="utf-8")
    return p


def test_load_watches_parses_both_target_kinds(tmp_path):
    path = _write_watches(tmp_path, [
        {"id": "a", "site": "scene", "movie_slug": "the-odyssey",
         "target": {"weekday": "friday"}},
        {"id": "b", "site": "vox", "movie_slug": "spider-man-brand-new-day",
         "cinema_slug": "city-centre-almaza", "target": {"date": "2026-08-06"}},
    ])
    a, b = load_watches(path, "Africa/Cairo")
    assert a.target_weekday == "friday" and a.target_date is None
    # scene base_url defaults and gets the movie slug substituted in.
    assert a.base_url.endswith("/movie-details/the-odyssey.html")
    assert b.target_date == date(2026, 8, 6) and b.target_weekday == ""
    assert b.timezone == "Africa/Cairo"


@pytest.mark.parametrize("entry,message", [
    ({"site": "scene", "movie_slug": "m", "target": {"weekday": "friday"}}, "missing 'id'"),
    ({"id": "a", "site": "netflix", "movie_slug": "m", "target": {"weekday": "friday"}}, "site must be"),
    ({"id": "a", "site": "vox", "movie_slug": "m", "target": {"weekday": "friday"}}, "requires 'cinema_slug'"),
    ({"id": "a", "site": "scene", "movie_slug": "m", "target": {}}, "exactly one of"),
    ({"id": "a", "site": "scene", "movie_slug": "m",
      "target": {"weekday": "friday", "date": "2026-08-06"}}, "exactly one of"),
    ({"id": "a", "site": "scene", "movie_slug": "m", "target": {"weekday": "funday"}}, "weekday must be"),
    ({"id": "a", "site": "scene", "movie_slug": "m", "target": {"date": "06-08-2026"}}, "must be YYYY-MM-DD"),
])
def test_load_watches_rejects_bad_config(tmp_path, entry, message):
    path = _write_watches(tmp_path, [entry])
    with pytest.raises(RuntimeError, match=message):
        load_watches(path, "Africa/Cairo")


def test_load_watches_rejects_duplicate_ids(tmp_path):
    path = _write_watches(tmp_path, [
        {"id": "dup", "site": "scene", "movie_slug": "m", "target": {"weekday": "friday"}},
        {"id": "dup", "site": "scene", "movie_slug": "n", "target": {"weekday": "friday"}},
    ])
    with pytest.raises(RuntimeError, match="duplicate id"):
        load_watches(path, "Africa/Cairo")


def test_repo_watches_file_is_valid():
    """The committed watches.json must actually load — it's what production runs on."""
    watches = load_watches(REPO_ROOT / "watches.json", "Africa/Cairo")
    by_id = {w.id: w for w in watches}
    avengers = by_id["avengers-doomsday"]
    assert isinstance(avengers, MovieWatch)
    assert avengers.movie == {"vox": ["avengers-doomsday", "avengers-doomsday-infinity-vision"],
                              "scene": []}
    assert avengers.cinemas == "all"
    assert avengers.channels == ["telegram", "discord"]

    # The VOX watches are fixed-date and get retired once their date passes, so
    # assert their shape rather than any one date — pinning a specific date here
    # fails the suite the moment a spent watch is cleaned out of watches.json.
    vox = [w for w in watches if isinstance(w, Watch) and w.site == "vox"]
    assert vox, "at least one VOX watch must stay configured"
    for w in vox:
        assert w.cinema_slug, f"{w.id}: VOX watches require a cinema_slug"
        assert w.notify_on == "bookable", f"{w.id}: VOX watches alert on bookable"


# ------------------------------------------------------------------ notify triggers


@pytest.mark.parametrize("total,bookable,expected", [
    (0, 0, False),   # nothing published yet
    (5, 0, True),    # published but not on sale — "published" doesn't care
    (5, 2, True),
])
def test_should_notify_published_fires_as_soon_as_anything_is_listed(total, bookable, expected):
    assert should_notify("published", total, bookable) is expected


@pytest.mark.parametrize("total,bookable,expected", [
    (0, 0, False),   # nothing published yet
    (5, 0, False),   # published but sales not open — keep waiting
    (5, 1, True),    # one bookable seat is enough
])
def test_should_notify_bookable_waits_for_sales_to_open(total, bookable, expected):
    assert should_notify("bookable", total, bookable) is expected


def test_bookable_watch_holds_off_on_an_all_unavailable_vox_page():
    """The real scenario: VOX lists the date but nothing is on sale yet."""
    groups = parse_showtimes_vox(_read("vox_with_showtimes.html"))
    # Drop the bookable Standard group — leaves only `unavailable` spans.
    listed_only = {k: v for k, v in groups.items() if k != "Standard"}
    total = sum(len(v) for v in listed_only.values())
    bookable = sum(1 for v in listed_only.values() for s in v if not s.soldout)
    assert total > 0 and bookable == 0
    assert should_notify("bookable", total, bookable) is False
    # ...and fires once anything opens up.
    assert should_notify("bookable", total + 1, 1) is True


def test_load_watches_defaults_notify_on_to_published(tmp_path):
    path = _write_watches(tmp_path, [
        {"id": "a", "site": "scene", "movie_slug": "m", "target": {"weekday": "friday"}},
    ])
    assert load_watches(path, "Africa/Cairo")[0].notify_on == "published"


@pytest.mark.parametrize("options,channels,mentions", [
    ({}, ["telegram", "discord"], []),
    ({"channels": ["discord"], "mention": ["123", "456"]}, ["discord"], ["123", "456"]),
    ({"channels": ["telegram"], "mention": []}, ["telegram"], []),
])
def test_load_watches_channel_and_mention_options(tmp_path, options, channels, mentions):
    path = _write_watches(tmp_path, [
        {"id": "a", "site": "scene", "movie_slug": "m",
         "target": {"weekday": "friday"}, **options},
    ])
    watch = load_watches(path)[0]
    assert watch.channels == channels
    assert watch.mention == mentions


@pytest.mark.parametrize("options,message", [
    ({"channels": []}, "channels must be a non-empty list"),
    ({"channels": "discord"}, "channels must be a non-empty list"),
    ({"channels": None}, "channels must be a non-empty list"),
    ({"channels": ["email"]}, "unknown channel"),
    ({"channels": [123]}, "unknown channel"),
    ({"channels": [["discord"]]}, "unknown channel"),
    ({"channels": ["discord", "discord"]}, "duplicate channel"),
    ({"mention": "123"}, "mention must be a list"),
    ({"mention": None}, "mention must be a list"),
    ({"mention": [123]}, "Discord user ID strings"),
    ({"mention": [True]}, "Discord user ID strings"),
    ({"mention": [""]}, "Discord user ID strings"),
    ({"mention": [" 123"]}, "Discord user ID strings"),
    ({"mention": ["-123"]}, "Discord user ID strings"),
    ({"mention": ["1.2"]}, "Discord user ID strings"),
    ({"mention": ["\u0661\u0662\u0663"]}, "Discord user ID strings"),
    ({"mention": ["@everyone"]}, "Discord user ID strings"),
    ({"mention": ["<@123>"]}, "Discord user ID strings"),
])
def test_load_watches_rejects_bad_channels_and_mentions(tmp_path, options, message):
    path = _write_watches(tmp_path, [
        {"id": "a", "site": "scene", "movie_slug": "m",
         "target": {"weekday": "friday"}, **options},
    ])
    with pytest.raises(RuntimeError, match=message):
        load_watches(path)


@pytest.mark.parametrize("routes,env,missing", [
    ([["telegram"]], {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"}, []),
    ([["discord"]], {"DISCORD_WEBHOOK_URL": "https://discord.example/webhook"}, []),
    ([["telegram", "discord"]], {},
     ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"]),
    ([["telegram"], ["discord"]], {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"},
     ["DISCORD_WEBHOOK_URL"]),
    ([["discord"], ["telegram"]], {"DISCORD_WEBHOOK_URL": "url"},
     ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]),
    ([["telegram"]], {"TELEGRAM_BOT_TOKEN": "token"}, ["TELEGRAM_CHAT_ID"]),
    ([["telegram"]], {"TELEGRAM_CHAT_ID": "chat"}, ["TELEGRAM_BOT_TOKEN"]),
    ([["discord"]], {"DISCORD_WEBHOOK_URL": ""}, ["DISCORD_WEBHOOK_URL"]),
    ([["discord"]], {"DISCORD_WEBHOOK_URL": "   "}, ["DISCORD_WEBHOOK_URL"]),
])
def test_credentials_required_only_for_routed_channels(routes, env, missing):
    watches = [Watch(id=str(i), site="scene", movie_slug="m", timezone="Africa/Cairo",
                     channels=channels) for i, channels in enumerate(routes)]
    if missing:
        with pytest.raises(RuntimeError) as exc:
            load_config(watches, env)
        assert str(exc.value) == "Missing required env vars: " + ", ".join(missing)
    else:
        cfg = load_config(watches, env)
        assert cfg.telegram_token == env.get("TELEGRAM_BOT_TOKEN", "")
        assert cfg.telegram_chat_id == env.get("TELEGRAM_CHAT_ID", "")
        assert cfg.discord_webhook_url == env.get("DISCORD_WEBHOOK_URL", "")


@pytest.mark.parametrize("text,expected", [
    ("short\nmessage", ["short\nmessage"]),
    ("a" * 2000, ["a" * 2000]),
    ("a" * 1200 + "\n" + "b" * 1000, ["a" * 1200 + "\n", "b" * 1000]),
    ("a" * 1999 + "\n" + "b", ["a" * 1999 + "\n", "b"]),
    ("a" * 2001, ["a" * 2000, "a"]),
    ("<@123>\n" + "x" * 1500 + "\n\n" + "y" * 1500,
     ["<@123>\n" + "x" * 1500 + "\n\n", "y" * 1500]),
])
def test_split_discord_text_preserves_lines_and_fits_limit(text, expected):
    from check_showtimes import split_discord_text

    chunks = split_discord_text(text)
    assert chunks == expected
    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= 2000 for chunk in chunks)


def test_load_watches_rejects_bad_notify_on(tmp_path):
    path = _write_watches(tmp_path, [
        {"id": "a", "site": "scene", "movie_slug": "m", "target": {"weekday": "friday"},
         "notify_on": "whenever"},
    ])
    with pytest.raises(RuntimeError, match="notify_on must be"):
        load_watches(path, "Africa/Cairo")


# --------------------------------------------------------------------- message text


def test_message_uses_per_site_soldout_wording():
    """VOX's 'unavailable' covers not-yet-on-sale too, so it must not claim 'sold out'."""
    groups = {"GOLD": [Showtime(time="1:30pm", href="", soldout=True)]}
    vox_text = format_message("M", "C", date(2026, 8, 6), groups,
                              soldout_label=SITES["vox"].soldout_label)
    assert "(not bookable)" in vox_text and "sold out" not in vox_text

    scene_text = format_message("M", "C", date(2026, 8, 6), groups,
                                soldout_label=SITES["scene"].soldout_label)
    assert "(sold out)" in scene_text


def test_message_headline_defaults_are_backwards_compatible():
    """format_message must stay callable without the label/headline args."""
    groups = {"Standard": [Showtime(time="9:00pm", href="https://x/1")]}
    assert "showtimes are live!" in format_message("M", "C", date(2026, 8, 6), groups)


# ---------------------------------------------------------------------------- state


def test_load_state_migrates_legacy_flat_key(tmp_path):
    """Old state was a bare string for the single watch that existed then."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"notified_for": "20260724"}), encoding="utf-8")
    watches = [
        Watch(id="odyssey-d5-friday", site="scene", movie_slug="m", timezone="Africa/Cairo",
              target_weekday="friday"),
        Watch(id="other", site="vox", movie_slug="m", timezone="Africa/Cairo", cinema_slug="c",
              target_date=date(2026, 8, 6)),
    ]
    assert load_state(p, watches) == {"notified_for": {"odyssey-d5-friday": "20260724"}}


def test_load_state_reads_per_watch_map(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"notified_for": {"a": "20260731", "b": None}}), encoding="utf-8")
    assert load_state(p, []) == {"notified_for": {"a": "20260731", "b": None}}


def test_load_state_missing_file_is_empty(tmp_path):
    assert load_state(tmp_path / "nope.json", []) == {"notified_for": {}}

# ------------------------------------------------------------------------- run watch


def _fixture_fetch(pages):
    def fetch(url, headers):
        return _read(pages[url])
    return fetch


@pytest.mark.parametrize("site,fixture,url", [
    ("scene", "scene_no_showtimes.html",
     "https://district5.scenecinemas.com/movie-details/the-odyssey.html?business_day=31-07-2026&ajax=1"),
    ("vox", "vox_no_showtimes.html",
     "https://egy.voxcinemas.com/showtimes?c=city-centre-almaza&m=the-odyssey&d=20260731"),
])
def test_run_date_watch_not_yet_published_is_quiet(site, fixture, url, caplog):
    watch = Watch(id="w", site=site, movie_slug="the-odyssey", timezone="Africa/Cairo",
                  cinema_slug="city-centre-almaza", target_weekday="friday",
                  base_url="https://district5.scenecinemas.com/movie-details/the-odyssey.html")
    state = {"notified_for": {"other": "20260724"}}
    now = datetime(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    assert run_watch(watch, _fixture_fetch({url: fixture}), state, now) == ([], state, ())
    assert not caplog.records


def test_run_date_watch_published_alert_and_dedupe():
    watch = Watch(id="w", site="scene", movie_slug="the-odyssey", timezone="Africa/Cairo",
                  channels=["telegram"],
                  target_weekday="friday",
                  base_url="https://district5.scenecinemas.com/movie-details/the-odyssey.html")
    url = "https://district5.scenecinemas.com/movie-details/the-odyssey.html?business_day=31-07-2026&ajax=1"
    fetch = _fixture_fetch({url: "scene_with_showtimes.html"})
    state = {"notified_for": {"other": "20260724"}}
    now = datetime(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    alerts, next_state, failures = run_watch(watch, fetch, state, now)
    assert not failures
    assert len(alerts) == 1
    assert alerts[0].texts == {"telegram": (
        '\U0001f3ac <b>The Odyssey</b> showtimes are live!\n'
        '\U0001f4cd Scene Cinema - District 5\n'
        '\U0001f4c5 Friday, July 31, 2026\n\n'
        '<b>IMAX</b>\n'
        '\u2022 <s>04:00 PM</s> (sold out)\n'
        '\u2022 <a href="https://district5.scenecinemas.com/showtime-6a5fc5c9f395a1f0c8143b76">08:00 PM</a>\n'
        '\u2022 <s>12:00 AM</s> (sold out)\n\n'
        '<b>Premiere</b>\n'
        '\u2022 <s>05:15 PM</s> (sold out)\n'
        '\u2022 <s>08:30 PM</s> (sold out)\n'
        '\u2022 <s>12:00 AM</s> (sold out)\n\n'
        '<b>Standard & Deluxe</b>\n'
        '\u2022 <a href="https://district5.scenecinemas.com/showtime-6a634c63f395a1f0c818542c">04:00 PM</a>'
    )}
    assert alerts[0].mentions == []
    assert next_state == {"notified_for": {"other": "20260724", "w": "20260731"}}
    assert state == {"notified_for": {"other": "20260724"}}
    assert run_watch(watch, fetch, next_state, now) == ([], next_state, ())


def test_run_fixed_date_watch_holds_off_then_alerts_when_bookable():
    watch = Watch(id="w", site="vox", movie_slug="spider-man-brand-new-day",
                  channels=["telegram"],
                  timezone="Africa/Cairo", cinema_slug="city-centre-almaza",
                  target_date=date(2026, 8, 6), notify_on="bookable")
    url = "https://egy.voxcinemas.com/showtimes?c=city-centre-almaza&m=spider-man-brand-new-day&d=20260806"
    state = {"notified_for": {}}
    now = datetime(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    assert run_watch(watch, _fixture_fetch({url: "vox_not_bookable.html"}), state, now) == ([], state, ())
    alerts, next_state, failures = run_watch(watch, _fixture_fetch({url: "vox_with_showtimes.html"}), state, now)
    assert not failures
    assert alerts[0].texts == {"telegram": (
        '\U0001f3ac <b>Spider-Man: Brand New Day</b> is now bookable!\n'
        '\U0001f4cd City Centre Almaza\n'
        '\U0001f4c5 Thursday, August 6, 2026\n\n'
        '<b>GOLD</b>\n'
        '\u2022 <s>1:30pm</s> (not bookable)\n'
        '\u2022 <s>4:45pm</s> (not bookable)\n'
        '\u2022 <s>8:00pm</s> (not bookable)\n\n'
        '<b>4DX</b>\n'
        '\u2022 <s>12:45pm</s> (not bookable)\n'
        '\u2022 <s>4:00pm</s> (not bookable)\n\n'
        '<b>Standard</b>\n'
        '\u2022 <a href="https://egy.voxcinemas.com/booking/0047-266952">1:45pm</a>\n'
        '\u2022 <a href="https://egy.voxcinemas.com/booking/0047-266953">5:00pm</a>\n'
        '\u2022 <a href="https://egy.voxcinemas.com/booking/0047-266954">8:15pm</a>\n'
        '\u2022 <a href="https://egy.voxcinemas.com/booking/0047-266955">11:30pm</a>'
    )}
    assert len(alerts) == 1 and alerts[0].mentions == []
    assert next_state == {"notified_for": {"w": "20260806"}}
    assert state == {"notified_for": {}}
    assert run_watch(watch, _fixture_fetch({url: "vox_with_showtimes.html"}), next_state, now) == ([], next_state, ())


def test_run_date_watch_skips_past_fixed_date_without_fetching():
    watch = Watch(id="w", site="vox", movie_slug="m", timezone="Africa/Cairo",
                  cinema_slug="c", target_date=date(2026, 7, 26))
    state = {"notified_for": {}}
    now = datetime(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    assert run_watch(watch, _fixture_fetch({}), state, now) == ([], state, ())


def test_run_date_watch_requires_an_aware_clock():
    watch = Watch(id="w", site="scene", movie_slug="m", timezone="Africa/Cairo",
                  target_weekday="friday")
    with pytest.raises(ValueError, match="aware"):
        run_watch(watch, _fixture_fetch({}), {"notified_for": {}}, datetime(2026, 7, 27, 10))


@pytest.mark.parametrize("failure", ["fetch", "delivery", "discord", None])
def test_main_keeps_failed_watch_state_and_delivers_other_alerts(tmp_path, monkeypatch, failure):
    import check_showtimes as notifier

    watches_path = _write_watches(tmp_path, [
        {"id": wid, "site": "vox", "movie_slug": wid, "cinema_slug": "city-centre-almaza",
         "channels": ["telegram", "discord"], "mention": ["123"],
         "target": {"date": "2026-08-06"}} for wid in ("failed", "successful")
    ])
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"notified_for": {"failed": "20260724"}}), encoding="utf-8")
    monkeypatch.setenv("WATCHES_FILE", str(watches_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    monkeypatch.setattr(notifier, "DEFAULT_STATE_PATH", state_path)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo")).astimezone(tz)

    monkeypatch.setattr(notifier, "datetime", FrozenDatetime)
    pages = {
        f"https://egy.voxcinemas.com/showtimes?c=city-centre-almaza&m={wid}&d=20260806":
        "vox_with_showtimes.html" for wid in ("failed", "successful")
    }
    fetch_fixture = _fixture_fetch(pages)

    events = []
    monkeypatch.setattr(notifier.time, "sleep", lambda seconds: events.append(("sleep", seconds)))

    def fetch(url, headers):
        events.append(("fetch", url))
        if failure == "fetch" and "m=failed&" in url:
            raise RuntimeError("Site failed")
        return fetch_fixture(url, headers)

    delivered = []

    def send(token, chat_id, text):
        if failure == "delivery" and not delivered:
            delivered.append("failed delivery")
            raise RuntimeError("Channel failed")
        delivered.append(text)

    monkeypatch.setattr(notifier, "fetch_page", fetch)
    monkeypatch.setattr(notifier, "send_telegram", send)
    discord_delivered = []

    def send_discord(webhook_url, text, mentions):
        assert mentions == ["123"]
        discord_delivered.append(text)
        if failure == "discord" and len(discord_delivered) == 1:
            raise RuntimeError("Discord failed")

    monkeypatch.setattr(notifier, "send_discord", send_discord)
    assert notifier.main() == (1 if failure else 0)
    assert events == [("fetch", next(iter(pages))), ("sleep", 1),
                      ("fetch", list(pages)[1])]
    assert "showtimes are live!" in delivered[-1]
    assert len(discord_delivered) == (1 if failure == "fetch" else 2)
    assert discord_delivered[-1].startswith("<@123>\n")
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "notified_for": {"failed": "20260724" if failure else "20260806", "successful": "20260806"}
    }


def test_run_date_watch_warns_when_empty_page_markup_changes(caplog):
    watch = Watch(id="w", site="vox", movie_slug="m", timezone="Africa/Cairo",
                  cinema_slug="c", target_date=date(2026, 8, 6))
    url = "https://egy.voxcinemas.com/showtimes?c=c&m=m&d=20260806"
    state = {"notified_for": {}}
    now = datetime(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    assert run_watch(watch, _fixture_fetch({url: "vox_missing_empty_marker.html"}), state, now) == ([], state, ())
    assert "no showtimes AND no expected empty-state marker" in caplog.text


@pytest.mark.parametrize("target", [
    {"target_weekday": "friday"},
    {"target_date": date(2026, 7, 31)},
])
def test_run_published_date_watch_uses_watch_timezone_and_not_bookable_showtimes(target):
    watch = Watch(id="w", site="vox", movie_slug="m", timezone="Africa/Cairo",
                  cinema_slug="c", **target)
    url = "https://egy.voxcinemas.com/showtimes?c=c&m=m&d=20260731"
    state = {"notified_for": {}}
    # Thursday in UTC is already Friday in the Watch's timezone.
    now = datetime(2026, 7, 30, 22, tzinfo=ZoneInfo("UTC"))
    alerts, next_state, failures = run_watch(watch, _fixture_fetch({url: "vox_not_bookable.html"}), state, now)
    assert not failures
    assert len(alerts) == 1
    assert "showtimes are live!" in alerts[0].texts["telegram"]
    assert "Friday, July 31, 2026" in alerts[0].texts["telegram"]
    assert "(not bookable)" in alerts[0].texts["telegram"]
    assert next_state == {"notified_for": {"w": "20260731"}}
    assert state == {"notified_for": {}}


@pytest.mark.parametrize("routing,expected", [
    ({}, {"telegram", "discord"}),
    ({"channels": ["telegram"]}, {"telegram"}),
    ({"channels": ["discord"]}, {"discord"}),
    ({"channels": ["discord", "telegram"]}, {"telegram", "discord"}),
])
def test_run_date_watch_routes_alert_and_discord_mentions(routing, expected):
    watch = Watch(id="w", site="vox", movie_slug="spider-man-brand-new-day",
                  timezone="Africa/Cairo", cinema_slug="city-centre-almaza",
                  target_date=date(2026, 8, 6), notify_on="bookable",
                  mention=["123456789", "987654321"], **routing)
    url = "https://egy.voxcinemas.com/showtimes?c=city-centre-almaza&m=spider-man-brand-new-day&d=20260806"
    now = datetime(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    alerts, _, failures = run_watch(watch, _fixture_fetch({url: "vox_with_showtimes.html"}),
                          {"notified_for": {}}, now)
    assert not failures
    assert len(alerts) == 1
    alert = alerts[0]
    assert set(alert.texts) == expected
    assert alert.mentions == ["123456789", "987654321"]
    if "discord" in expected:
        text = alert.texts["discord"]
        assert text.startswith("<@123456789> <@987654321>\n")
        assert "**Spider-Man: Brand New Day** is now bookable!" in text
        assert "**GOLD**\n\u2022 ~~1:30pm~~ (not bookable)" in text
        assert "[1:45pm](https://egy.voxcinemas.com/booking/0047-266952)" in text
        assert "<b>" not in text and '<a href=' not in text
    if "telegram" in expected:
        assert '<a href="https://egy.voxcinemas.com/booking/0047-266952">1:45pm</a>' in alert.texts["telegram"]
        assert "<@" not in alert.texts["telegram"]


def test_sample_helper_uses_selected_watch_channels_and_mentions(tmp_path, monkeypatch):
    import check_showtimes as notifier
    import send_test_message as helper

    path = _write_watches(tmp_path, [
        {"id": "discord", "site": "scene", "movie_slug": "m",
         "target": {"weekday": "friday"}, "channels": ["discord"], "mention": ["123"]},
        {"id": "telegram", "site": "scene", "movie_slug": "m",
         "target": {"weekday": "friday"}, "channels": ["telegram"]},
    ])
    monkeypatch.setenv("WATCHES_FILE", str(path))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 27)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo")).astimezone(tz)

    monkeypatch.setattr(helper, "date", FrozenDate)
    monkeypatch.setattr(notifier, "datetime", FrozenDatetime)
    delivered = []

    def send_discord(webhook_url, text, mentions):
        delivered.append((webhook_url, text, mentions))

    monkeypatch.setattr(notifier, "send_discord", send_discord)
    assert helper.main(["discord"]) == 0
    assert len(delivered) == 1
    url, text, mentions = delivered[0]
    assert url == "https://discord.example/webhook"
    assert mentions == ["123"]
    assert "**TEST**" in text and "<@123>" in text
    assert "[04:00 PM](https://district5.scenecinemas.com/showtime-sample1)" in text
    assert "<b>" not in text

def test_run_scene_date_watch_at_cairo_festival_city(tmp_path):
    path = _write_watches(tmp_path, [{
        "id": "cfc", "site": "scene", "movie_slug": "resident_evil",
        "cinema_slug": "cairo-festival-city", "target": {"date": "2026-10-29"},
    }])
    watch = load_watches(path)[0]
    url = "https://cfc.scenecinemas.com/movie-details/resident_evil.html?business_day=29-10-2026&ajax=1"
    now = datetime(2026, 10, 28, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    alerts, state, failures = run_watch(watch, _fixture_fetch({url: "scene_cfc_with_showtimes.html"}),
                              {"notified_for": {}}, now)
    assert not failures
    assert len(alerts) == 1
    assert "Scene Cinema - Cairo Festival City" in alerts[0].texts["telegram"]
    assert state == {"notified_for": {"cfc": "20261029"}}


def test_run_existing_scene_date_watch_defaults_to_district_5(tmp_path):
    path = _write_watches(tmp_path, [{"id": "odyssey-d5-friday", "site": "scene",
                                      "movie_slug": "the-odyssey",
                                      "target": {"weekday": "friday"}}])
    watch = load_watches(path)[0]
    url = "https://district5.scenecinemas.com/movie-details/the-odyssey.html?business_day=31-07-2026&ajax=1"
    now = datetime(2026, 7, 27, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    alerts, state, failures = run_watch(watch, _fixture_fetch({url: "scene_with_showtimes.html"}),
                              {"notified_for": {}}, now)
    assert not failures
    assert "Scene Cinema - District 5" in alerts[0].texts["telegram"]
    assert state == {"notified_for": {"odyssey-d5-friday": "20260731"}}


def test_load_watches_rejects_unknown_scene_cinema(tmp_path):
    path = _write_watches(tmp_path, [{
        "id": "bad", "site": "scene", "movie_slug": "m", "cinema_slug": "unknown",
        "target": {"date": "2026-10-29"},
    }])
    with pytest.raises(RuntimeError, match="district-5.*cairo-festival-city|cairo-festival-city.*district-5"):
        load_watches(path)


def test_parse_scene_cairo_festival_city_showtimes():
    groups = parse_showtimes_scene(_read("scene_cfc_with_showtimes.html"))
    assert list(groups) == ["Standard"]
    assert [showtime.time for showtime in groups["Standard"]] == [
        "10:30 AM", "01:00 PM", "03:15 PM", "05:30 PM", "08:00 PM", "10:15 PM", "12:30 AM",
    ]
    assert all(not showtime.soldout and showtime.href.startswith("https://cfc.scenecinemas.com/")
               for showtime in groups["Standard"])

def test_run_scene_date_watch_with_base_url_override(tmp_path):
    path = _write_watches(tmp_path, [{
        "id": "cfc", "site": "scene", "movie_slug": "resident_evil",
        "cinema_slug": "cairo-festival-city", "target": {"date": "2026-10-29"},
        "base_url": "https://example.com/movie-details/{movie}.html",
    }])
    watch = load_watches(path)[0]
    url = "https://example.com/movie-details/resident_evil.html?business_day=29-10-2026&ajax=1"
    now = datetime(2026, 10, 28, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    alerts, state, failures = run_watch(watch, _fixture_fetch({url: "scene_cfc_with_showtimes.html"}),
                              {"notified_for": {}}, now)
    assert not failures
    assert "Scene Cinema - Cairo Festival City" in alerts[0].texts["telegram"]
    assert state == {"notified_for": {"cfc": "20261029"}}


# ---------------------------------------------------------------------- Movie watches

MOVIE_URL = "https://egy.voxcinemas.com/showtimes?m=avengers-doomsday-infinity-vision"


MOVIE_NOW = datetime(2026, 10, 28, 10, tzinfo=ZoneInfo("Africa/Cairo"))


def _movie_pages(first="vox_movie.html", slug="avengers-doomsday-infinity-vision"):
    url = f"https://egy.voxcinemas.com/showtimes?m={slug}"
    return {url: first, **{
        f"{url}&d=202612{day}": (
            "vox_movie_second_date.html" if day == 17
            else f"vox_movie_202612{day}.html") for day in range(17, 24)
    }}


def _movie_watch(tmp_path, **extra):
    entry = {"id": "avengers", "movie": {"vox": ["avengers-doomsday-infinity-vision"]},
             "cinemas": "all", **extra}
    return load_watches(_write_watches(tmp_path, [entry]))[0]


def test_run_movie_watch_first_run_alerts_across_all_dates_and_cinemas(tmp_path):
    watch = _movie_watch(tmp_path, mention=["123"])
    state = {"notified_for": {"date-watch": "20260731"}}
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(_movie_pages()), state, MOVIE_NOW,
                                 sleep=lambda seconds: None)
    assert not failures
    assert len(alerts) == 1
    assert alerts[0].mentions == ["123"] and set(alerts[0].texts) == {"telegram", "discord"}
    discord = alerts[0].texts["discord"]
    assert discord.startswith("<@123>" + chr(10)) and "82 New Showtimes" in discord and "<b>" not in discord
    assert "](https://egy.voxcinemas.com/booking/" in discord
    text = alerts[0].texts["telegram"]
    assert "is now bookable" in text and "82 New Showtimes" in text
    assert text.index("City Centre Alexandria") < text.index("City Centre Almaza") < text.index("Mall of Egypt")
    assert "Wednesday, December 16, 2026" in text and "Wednesday, December 23, 2026" in text
    assert '<b>MAX</b>' in text and '<b>Standard</b>' in text
    assert '<a href="https://egy.voxcinemas.com/booking/0032-169550">11:00am</a>' in text
    assert '0047-276798' not in text
    assert proposed["movie_alerted"]["avengers"]["0032-169550"] == "20261216"
    assert len(proposed["movie_alerted"]["avengers"]) == 82
    assert proposed["notified_for"] == state["notified_for"]
    assert state == {"notified_for": {"date-watch": "20260731"}}


def test_movie_watch_state_round_trip_preserves_date_watches(tmp_path):
    from check_showtimes import save_state
    state = {"notified_for": {"date-watch": "20260731"},
             "movie_alerted": {"avengers": {"0032-169550": "20261216"}}}
    path = tmp_path / "state.json"
    save_state(path, state)
    assert load_state(path, []) == state


def test_run_movie_watch_alerts_once_when_not_bookable_becomes_bookable(tmp_path):
    watch = _movie_watch(tmp_path)
    state = {"notified_for": {}}
    _, state, failures = run_watch(watch, _fixture_fetch(_movie_pages()), state, MOVIE_NOW, sleep=lambda _: None)
    assert not failures
    alerts, state, failures = run_watch(watch, _fixture_fetch(_movie_pages("vox_movie_now_bookable.html")),
                              state, MOVIE_NOW, sleep=lambda _: None)
    assert not failures
    assert len(alerts) == 1 and "1 New Showtime\n" in alerts[0].texts["telegram"]
    assert 'booking/0047-276798' in alerts[0].texts["telegram"]
    assert len(state["movie_alerted"]["avengers"]) == 83
    assert run_watch(watch, _fixture_fetch(_movie_pages()), state, MOVIE_NOW, sleep=lambda _: None) == ([], state, ())
    assert run_watch(watch, _fixture_fetch(_movie_pages("vox_movie_now_bookable.html")),
                     state, MOVIE_NOW, sleep=lambda _: None) == ([], state, ())


def test_run_movie_watch_keeps_other_slugs_and_dates_after_failures(tmp_path, caplog):
    watch = _movie_watch(tmp_path, movie={"vox": ["broken", "avengers-doomsday-infinity-vision"]})
    pages = _movie_pages()
    del pages[f"{MOVIE_URL}&d=20261217"]
    state = {"notified_for": {}}
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), state, MOVIE_NOW, sleep=lambda _: None)
    assert failures
    assert len(alerts) == 1 and "73 New Showtimes" in alerts[0].texts["telegram"]
    assert len(proposed["movie_alerted"]["avengers"]) == 73
    assert "0032-169554" not in proposed["movie_alerted"]["avengers"]
    assert "broken" in caplog.text and "20261217" in caplog.text
    assert state == {"notified_for": {}}


@pytest.mark.parametrize("fixture,warning", [
    ("vox_no_showtimes.html", False), ("vox_missing_empty_marker.html", True),
])
def test_run_movie_watch_empty_page_warns_only_without_marker(tmp_path, caplog, fixture, warning):
    watch = _movie_watch(tmp_path)
    state = {"notified_for": {}}
    assert run_watch(watch, _fixture_fetch({MOVIE_URL: fixture}), state, MOVIE_NOW,
                     sleep=lambda _: None) == ([], state, ())
    assert ("no showtimes AND no expected empty-state marker" in caplog.text) is warning


@pytest.mark.parametrize("fail_after", [None, 0, 1])
def test_main_movie_watch_partial_failure_commits_only_delivered_alerts(tmp_path, monkeypatch, fail_after):
    import check_showtimes as notifier
    watches_path = _write_watches(tmp_path, [{"id": "avengers", "movie": {
        "vox": ["broken", "avengers-doomsday-infinity-vision"]}, "cinemas": "all", "channels": ["telegram"]}])
    state = {"notified_for": {"date-watch": "20260731"}}
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("WATCHES_FILE", str(watches_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr(notifier, "DEFAULT_STATE_PATH", state_path)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return MOVIE_NOW.astimezone(tz)

    monkeypatch.setattr(notifier, "datetime", FrozenDatetime)
    monkeypatch.setattr(notifier.time, "sleep", lambda _: None)
    monkeypatch.setattr(notifier, "fetch_page", _fixture_fetch(_movie_pages()))
    delivered = []

    def send(token, chat_id, text):
        if fail_after is not None and len(delivered) == fail_after:
            raise RuntimeError("Channel failed")
        delivered.append(text)

    monkeypatch.setattr(notifier, "send_telegram", send)
    assert notifier.main() == 1
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["notified_for"] == state["notified_for"]
    if fail_after is not None:
        assert saved == state
    else:
        assert len(delivered) > 1
        assert all(len(text) <= 4096 for text in delivered)
        assert "82 New Showtimes" in delivered[0]
        assert len(saved["movie_alerted"]["avengers"]) == 82


@pytest.mark.parametrize("extra,message", [
    ({"target": {"date": "2026-12-16"}}, "cannot mix"),
    ({"site": "vox"}, "cannot mix"),
    ({"movie_slug": "avengers"}, "cannot mix"),
    ({"cinema_slug": "mall-of-egypt"}, "cannot mix"),
    ({"notify_on": "published"}, "cannot mix"),
    ({"movie": {"vox": [], "scene": []}}, "at least one Movie slug"),
    ({"movie": {"unknown": ["avengers"]}}, "unknown Site"),
    ({"movie": {"vox": "avengers"}}, "list of non-empty strings"),
    ({"movie": {"vox": [""]}}, "list of non-empty strings"),
    ({"cinemas": ["unknown"]}, "unknown Cinema"),
    ({"cinemas": []}, "cinemas must"),
    ({"timezone": "Unknown/Timezone"}, "unknown timezone"),
])
def test_movie_watch_rejects_bad_config(tmp_path, extra, message):
    with pytest.raises(RuntimeError, match=message):
        _movie_watch(tmp_path, **extra)


def test_run_movie_watch_combines_multiple_slugs_in_one_alert(tmp_path):
    watch = _movie_watch(tmp_path, movie={"vox": ["avengers-doomsday-infinity-vision", "avengers-doomsday"], "scene": []})
    pages = {**_movie_pages(), **_movie_pages("vox_movie_now_bookable.html", "avengers-doomsday")}
    alerts, state, failures = run_watch(watch, _fixture_fetch(pages), {"notified_for": {}}, MOVIE_NOW, sleep=lambda _: None)
    assert not failures
    assert len(alerts) == 1 and "83 New Showtimes" in alerts[0].texts["telegram"]
    assert len(state["movie_alerted"]["avengers"]) == 83


def test_run_movie_watch_ignores_past_dates(tmp_path):
    watch = _movie_watch(tmp_path)
    state = {"notified_for": {}}
    now = datetime(2026, 12, 24, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    assert run_watch(watch, _fixture_fetch({MOVIE_URL: "vox_movie.html"}), state, now,
                     sleep=lambda _: None) == ([], state, ())


def test_run_movie_watch_with_only_not_bookable_showtimes_is_quiet(tmp_path):
    watch = _movie_watch(tmp_path)
    pages = _movie_pages("vox_movie_not_bookable.html")
    pages.update({f"{MOVIE_URL}&d=202612{day}": "vox_no_showtimes.html" for day in range(17, 24)})
    state = {"notified_for": {"date-watch": "20260731"}}
    assert run_watch(watch, _fixture_fetch(pages), state, MOVIE_NOW,
                     sleep=lambda _: None) == ([], state, ())


def test_vox_movie_date_picker_includes_bare_current_date_and_every_link():
    from check_showtimes import vox_movie_dates
    assert vox_movie_dates(_read("vox_movie.html")) == [date(2026, 12, day) for day in range(16, 24)]
    assert vox_movie_dates(_read("vox_movie_second_date.html")) == [date(2026, 12, day) for day in range(16, 24)]

SCENE_MOVIE_NOW = datetime(2026, 10, 27, 10, tzinfo=ZoneInfo("Africa/Cairo"))


def _scene_movie_pages():
    pages = {}
    for host in ("district5", "cfc"):
        url = f"https://{host}.scenecinemas.com/movie-details/resident_evil.html"
        pages[url] = f"scene_movie_{host}.html"
        for day in (27, 28):
            pages[f"{url}?business_day={day}-10-2026&ajax=1"] = f"scene_movie_{host}_{day}-10-2026.html"
    return pages

def test_run_scene_movie_watch_discovers_both_cinemas_and_dates_and_alerts_once(tmp_path):
    watch = _movie_watch(tmp_path, movie={"scene": ["resident_evil"], "vox": []})
    initial = {"notified_for": {"date-watch": "20260731"}}
    fetch = _fixture_fetch(_scene_movie_pages())
    alerts, proposed, failures = run_watch(watch, fetch, initial, SCENE_MOVIE_NOW)
    assert not failures
    assert len(alerts) == 1
    for channel, text in alerts[0].texts.items():
        assert "Resident Evil" in text and "11 New Showtimes" in text
        assert "District 5" in text and "Cairo Festival City" in text
        assert "Tuesday, October 27, 2026" in text and "Wednesday, October 28, 2026" in text
        assert "Standard &amp; Deluxe" in text if channel == "telegram" else "Standard & Deluxe" in text
        assert "https://district5.scenecinemas.com/movie-details/resident_evil.html" in text
        assert "https://cfc.scenecinemas.com/movie-details/resident_evil.html" in text
        assert "/showtime-" not in text
    assert len(proposed["movie_alerted"]["avengers"]) == 11
    assert set(proposed["movie_alerted"]["avengers"].values()) == {"20261027", "20261028"}
    assert proposed["notified_for"] == initial["notified_for"]
    assert initial == {"notified_for": {"date-watch": "20260731"}}
    assert run_watch(watch, fetch, proposed, SCENE_MOVIE_NOW) == ([], proposed, ())


def test_run_scene_movie_watch_soldout_never_alerts_and_later_bookable_alerts_once(tmp_path):
    watch = _movie_watch(tmp_path, movie={"scene": ["resident_evil"]})
    pages = _scene_movie_pages()
    url = "https://district5.scenecinemas.com/movie-details/resident_evil.html?business_day=28-10-2026&ajax=1"
    pages[url] = "scene_movie_district5_soldout.html"
    alerts, state, failures = run_watch(watch, _fixture_fetch(pages), {"notified_for": {}}, SCENE_MOVIE_NOW)
    assert not failures
    assert "9 New Showtimes" in alerts[0].texts["telegram"]
    district = alerts[0].texts["telegram"].split("Cairo Festival City")[0]
    assert "08:00 PM" not in district and "10:00 PM" not in district
    bookable = _fixture_fetch(_scene_movie_pages())
    alerts, state, failures = run_watch(watch, bookable, state, SCENE_MOVIE_NOW)
    assert not failures
    assert len(alerts) == 1 and "2 New Showtimes" in alerts[0].texts["telegram"]
    assert "08:00 PM" in alerts[0].texts["telegram"] and "10:00 PM" in alerts[0].texts["telegram"]
    assert len(state["movie_alerted"]["avengers"]) == 11
    assert run_watch(watch, _fixture_fetch(pages), state, SCENE_MOVIE_NOW) == ([], state, ())
    assert run_watch(watch, bookable, state, SCENE_MOVIE_NOW) == ([], state, ())

@pytest.mark.parametrize("cinemas,expected,excluded", [
    (["district-5", "city-centre-almaza"], ["District 5", "City Centre Almaza"],
     ["Cairo Festival City", "City Centre Alexandria", "Mall of Egypt"]),
    (["cairo-festival-city"], ["Cairo Festival City"],
     ["District 5", "City Centre Almaza", "City Centre Alexandria", "Mall of Egypt"]),
])
def test_run_movie_watch_cinema_list_filters_across_sites(tmp_path, cinemas, expected, excluded):
    watch = _movie_watch(tmp_path, movie={"scene": ["resident_evil"],
                                        "vox": ["avengers-doomsday-infinity-vision"]}, cinemas=cinemas)
    pages = {**_scene_movie_pages(), **_movie_pages()}
    skipped = "cfc" if "district-5" in cinemas else "district5"
    pages = {url: fixture for url, fixture in pages.items() if f"https://{skipped}." not in url}
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), {"notified_for": {}}, SCENE_MOVIE_NOW)
    assert not failures
    assert len(alerts) == 1
    text = alerts[0].texts["telegram"]
    assert all(name in text for name in expected)
    assert all(name not in text for name in excluded)
    assert len(proposed["movie_alerted"]["avengers"]) > 0


@pytest.mark.parametrize("failed_site,count", [(None, 93), ("vox", 11), ("scene", 82)])
def test_run_movie_watch_combines_sites_and_keeps_successful_site_after_failure(tmp_path, failed_site, count):
    watch = _movie_watch(tmp_path, movie={"vox": ["avengers-doomsday-infinity-vision"],
                                        "scene": ["resident_evil"]})
    pages = {**_movie_pages(), **_scene_movie_pages()}
    if failed_site:
        pages = {url: fixture for url, fixture in pages.items()
                 if ("voxcinemas.com" if failed_site == "vox" else "scenecinemas.com") not in url}
    initial = {"notified_for": {}}
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), initial, SCENE_MOVIE_NOW)
    assert bool(failures) is bool(failed_site)
    assert len(alerts) == 1
    assert len(proposed["movie_alerted"]["avengers"]) == count
    for text in alerts[0].texts.values():
        assert f"{count} New Showtime{'' if count == 1 else 's'}\n" in text
        assert ("City Centre Almaza" in text) is (failed_site != "vox")
        assert ("District 5" in text) is (failed_site != "scene")
        assert ("Cairo Festival City" in text) is (failed_site != "scene")
        if not failed_site:
            assert text.count("City Centre Almaza") == 1 and text.count("District 5") == 1
    assert initial == {"notified_for": {}}

@pytest.mark.parametrize("failed_site,count", [("vox", 11), ("scene", 82)])
def test_main_cross_site_failure_delivers_successful_site_and_exits_nonzero(tmp_path, monkeypatch, failed_site, count):
    import check_showtimes as notifier
    path = _write_watches(tmp_path, [{"id": "combined", "movie": {
        "vox": ["avengers-doomsday-infinity-vision"], "scene": ["resident_evil"]},
        "cinemas": "all", "channels": ["discord"]}])
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("WATCHES_FILE", str(path))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    monkeypatch.setattr(notifier, "DEFAULT_STATE_PATH", state_path)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return SCENE_MOVIE_NOW.astimezone(tz)

    monkeypatch.setattr(notifier, "datetime", FrozenDatetime)
    monkeypatch.setattr(notifier.time, "sleep", lambda _: None)
    pages = _scene_movie_pages() if failed_site == "vox" else _movie_pages()
    monkeypatch.setattr(notifier, "fetch_page", _fixture_fetch(pages))
    delivered = []
    monkeypatch.setattr(notifier, "send_discord", lambda url, text, mentions: delivered.append(text))
    assert notifier.main() == 1
    assert len(delivered) == 1 and f"{count} New Showtimes" in delivered[0]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(saved["movie_alerted"]["combined"]) == count


def test_run_scene_movie_watch_identity_includes_experience_but_not_movie_slug(tmp_path):
    watch = _movie_watch(tmp_path, movie={"scene": ["resident_evil", "resident-evil-variant"]})
    pages = _scene_movie_pages()
    pages.update({url.replace("resident_evil", "resident-evil-variant"): fixture
                  for url, fixture in list(pages.items())})
    alerts, state, failures = run_watch(watch, _fixture_fetch(pages), {"notified_for": {}}, SCENE_MOVIE_NOW)
    assert not failures
    assert len(alerts) == 1 and "11 New Showtimes" in alerts[0].texts["telegram"]
    # These same Cinemas, dates and times are already alerted for both slugs.
    pages["https://district5.scenecinemas.com/movie-details/resident-evil-variant.html?business_day=28-10-2026&ajax=1"] = "scene_movie_district5_premiere.html"
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), state, SCENE_MOVIE_NOW)
    assert not failures
    text = alerts[0].texts["telegram"]
    assert len(alerts) == 1 and "3 New Showtimes" in text and "<b>Premiere</b>" in text
    assert "08:00 PM" in text and "10:00 PM" in text and "12:15 AM" in text
    assert "Standard" not in text and "Cairo Festival City" not in text
    assert len(proposed["movie_alerted"]["avengers"]) == 14
    assert run_watch(watch, _fixture_fetch(pages), proposed, SCENE_MOVIE_NOW) == ([], proposed, ())


@pytest.mark.parametrize("cinemas,names,excluded,count", [
    (["city-centre-alexandria"], ["City Centre Alexandria"], ["City Centre Almaza", "Mall of Egypt"], 4),
    (["city-centre-almaza", "mall-of-egypt"], ["City Centre Almaza"], ["City Centre Alexandria"], 1),
])
def test_run_movie_watch_limits_alerts_and_state_to_listed_cinemas(tmp_path, cinemas, names, excluded, count):
    watch = _movie_watch(tmp_path, cinemas=cinemas)
    pages = _movie_pages()
    pages.update({f"{MOVIE_URL}&d=202612{day}": "vox_no_showtimes.html" for day in range(17, 24)})
    state = {"notified_for": {"date-watch": "20260731"}}
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), state, MOVIE_NOW)
    assert not failures
    assert len(alerts) == 1
    for text in alerts[0].texts.values():
        assert f"{count} New Showtime{'' if count == 1 else 's'}\n" in text
        assert all(name in text for name in names)
        assert all(name not in text for name in excluded)
    expected = ({"0032-169550": "20261216", "0032-169551": "20261216",
                 "0032-169552": "20261216", "0032-169553": "20261216"}
                if count == 4 else {"0047-276799": "20261216"})
    assert proposed == {**state, "movie_alerted": {"avengers": expected}}
    assert state == {"notified_for": {"date-watch": "20260731"}}


def test_run_movie_watch_matches_normalised_cinema_names(tmp_path):
    watch = _movie_watch(tmp_path, cinemas=["city-centre-alexandria"])
    # Derived in memory from saved HTML: vary only Cinema name case and separators.
    html = ("<!-- Derived from vox_movie.html: normalised Cinema name test. -->" +
            _read("vox_movie.html").replace("City Centre Alexandria", " CITY-CENTRE   ALEXANDRIA "))
    pages = {MOVIE_URL: html,
             **{f"{MOVIE_URL}&d=202612{day}": _read("vox_no_showtimes.html") for day in range(17, 24)}}
    alerts, state, failures = run_watch(watch, lambda url, headers: pages[url], {"notified_for": {}}, MOVIE_NOW)
    assert not failures
    assert len(alerts) == 1 and "4 New Showtimes" in alerts[0].texts["telegram"]
    assert "City Centre Almaza" not in alerts[0].texts["telegram"]
    assert state["movie_alerted"] == {"avengers": {
        "0032-169550": "20261216", "0032-169551": "20261216",
        "0032-169552": "20261216", "0032-169553": "20261216"}}


@pytest.mark.parametrize("slug", ["typo-cinema", "district-5", ""])
def test_movie_watch_rejects_unknown_cinema_slugs_for_active_sites(tmp_path, slug):
    with pytest.raises(RuntimeError, match="unknown Cinema.*vox.*city-centre-alexandria.*city-centre-almaza.*mall-of-egypt"):
        _movie_watch(tmp_path, cinemas=[slug], movie={"vox": ["avengers"], "scene": []})


@pytest.mark.parametrize("hour,minute,expired", [(21, 59, False), (22, 0, True)])
def test_run_movie_watch_until_is_inclusive_in_watch_timezone(tmp_path, caplog, hour, minute, expired):
    watch = _movie_watch(tmp_path, until="2026-12-16")
    state = {"notified_for": {"date-watch": "20260731"}}
    now = datetime(2026, 12, 16, hour, minute, tzinfo=ZoneInfo("UTC"))
    caplog.set_level("INFO", logger="showtime_notifier")
    alerts, proposed, failures = run_watch(watch, _fixture_fetch({} if expired else _movie_pages()), state, now)
    assert not failures
    if expired:
        assert (alerts, proposed) == ([], state)
        assert "avengers" in caplog.text and "2026-12-16" in caplog.text and "skipping" in caplog.text
    else:
        assert len(alerts) == 1 and "82 New Showtimes" in alerts[0].texts["telegram"]
        assert len(proposed["movie_alerted"]["avengers"]) == 82


@pytest.mark.parametrize("until", ["16-12-2026", "2026-02-30", "", None, 123, [], {}])
def test_movie_watch_rejects_malformed_until(tmp_path, until):
    with pytest.raises(RuntimeError, match="until must be YYYY-MM-DD"):
        _movie_watch(tmp_path, until=until)


def test_date_watch_rejects_movie_watch_until(tmp_path):
    path = _write_watches(tmp_path, [{
        "id": "date-watch", "site": "vox", "movie_slug": "m", "cinema_slug": "city-centre-almaza",
        "target": {"date": "2026-12-16"}, "until": "2026-12-17",
    }])
    with pytest.raises(RuntimeError, match="cannot mix"):
        load_watches(path)


def test_run_movie_watch_prunes_past_dates_and_keeps_today_in_watch_timezone(tmp_path):
    watch = _movie_watch(tmp_path)
    state = {"notified_for": {"date-watch": "20260731"}, "movie_alerted": {
        "avengers": {"past": "20261215", "0032-169550": "20261216", "future": "20261224"},
        "other-watch": {"other": "20261215"}}}
    original = json.loads(json.dumps(state))
    pages = _movie_pages()
    pages.update({f"{MOVIE_URL}&d=202612{day}": "vox_no_showtimes.html" for day in range(17, 24)})
    now = datetime(2026, 12, 15, 22, tzinfo=ZoneInfo("UTC"))  # Dec 16 in Cairo.
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), state, now)
    assert not failures
    assert len(alerts) == 1 and "4 New Showtimes" in alerts[0].texts["telegram"]
    assert 'booking/0032-169550' not in alerts[0].texts["telegram"]
    assert proposed == {"notified_for": {"date-watch": "20260731"}, "movie_alerted": {
        "avengers": {"0032-169550": "20261216", "0032-169551": "20261216",
                     "0032-169552": "20261216", "0032-169553": "20261216",
                     "0047-276799": "20261216", "future": "20261224"},
        "other-watch": {"other": "20261215"}}}
    assert state == original


@pytest.mark.parametrize("expired", [False, True])
def test_run_movie_watch_keeps_state_without_new_showtimes(tmp_path, expired):
    watch = _movie_watch(tmp_path, **({"until": "2026-12-15"} if expired else {}))
    state = {"notified_for": {"date-watch": "20260731"}, "movie_alerted": {
        "avengers": {"past": "20261215", "today": "20261216", "future": "20261217"}}}
    original = json.loads(json.dumps(state))
    now = datetime(2026, 12, 15, 22, tzinfo=ZoneInfo("UTC"))
    alerts, proposed, failures = run_watch(watch, _fixture_fetch({} if expired else {MOVIE_URL: "vox_no_showtimes.html"}),
                                 state, now)
    assert not failures
    assert alerts == []
    assert proposed == original
    assert state == original


def test_run_movie_watch_listed_cinema_starts_showing_on_later_date(tmp_path):
    watch = _movie_watch(tmp_path, cinemas=["mall-of-egypt"])
    pages = _movie_pages()
    pages.update({f"{MOVIE_URL}&d=202612{day}": "vox_no_showtimes.html" for day in range(18, 24)})
    state = {"notified_for": {}}
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), state, MOVIE_NOW)
    assert not failures
    assert len(alerts) == 1
    for text in alerts[0].texts.values():
        assert "4 New Showtimes" in text and "Mall of Egypt" in text
        assert "December 17, 2026" in text and "December 16, 2026" not in text
        assert "City Centre" not in text
    assert proposed == {"notified_for": {}, "movie_alerted": {"avengers": {
        "0028-568252": "20261217", "0028-568253": "20261217",
        "0028-568254": "20261217", "0028-568255": "20261217"}}}
    # Excluded Cinemas have no Bookable Showtimes recorded, so widening the list arms them.
    wider = _movie_watch(tmp_path, cinemas="all")
    alerts, proposed, failures = run_watch(wider, _fixture_fetch(pages), proposed, MOVIE_NOW)
    assert not failures
    assert len(alerts) == 1 and "10 New Showtimes" in alerts[0].texts["telegram"]
    assert "Mall of Egypt" not in alerts[0].texts["telegram"]
    assert len(proposed["movie_alerted"]["avengers"]) == 14


def test_run_movie_watch_mixed_vox_and_scene_slugs_filter_both_sites(tmp_path):
    watch = _movie_watch(tmp_path, movie={"scene": ["resident_evil"],
                                        "vox": ["avengers-doomsday-infinity-vision"]},
                         cinemas=["district-5", "city-centre-almaza"])
    pages = {**_scene_movie_pages(), **_movie_pages()}
    pages = {url: fixture for url, fixture in pages.items() if "https://cfc." not in url}
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), {"notified_for": {}}, SCENE_MOVIE_NOW)
    assert not failures
    text = alerts[0].texts["telegram"]
    assert "District 5" in text and "City Centre Almaza" in text
    assert all(name not in text for name in ("Cairo Festival City", "City Centre Alexandria", "Mall of Egypt"))
    identities = proposed["movie_alerted"]["avengers"]
    assert any(i.startswith("scene:district-5:") for i in identities)
    assert not any(i.startswith("scene:cairo-festival-city:") for i in identities)


def test_run_movie_watch_prunes_past_dated_scene_identities(tmp_path):
    watch = _movie_watch(tmp_path, movie={"scene": ["resident_evil"]})
    state = {"notified_for": {}, "movie_alerted": {"avengers": {
        "scene:district-5:20261027:08:00 PM:Standard": "20261027",
        "scene:district-5:20261029:08:00 PM:Standard": "20261029"}}}
    now = datetime(2026, 10, 28, 10, tzinfo=ZoneInfo("Africa/Cairo"))
    _, proposed, failures = run_watch(watch, _fixture_fetch(_scene_movie_pages()), state, now)
    assert not failures
    kept = proposed["movie_alerted"]["avengers"]
    assert "scene:district-5:20261027:08:00 PM:Standard" not in kept
    assert "scene:district-5:20261029:08:00 PM:Standard" in kept


def test_sample_movie_alert_uses_watch_channels_and_mentions():
    from send_test_message import sample_movie_alert

    watch = MovieWatch(id="sample", movie={"vox": ["avengers-doomsday"]},
                       timezone="Africa/Cairo", channels=["discord"], mention=["123"])
    alert = sample_movie_alert(watch, date(2026, 12, 13))
    assert set(alert.texts) == {"discord"}
    assert alert.mentions == ["123"]
    text = alert.texts["discord"]
    assert "<@123>" in text and "4 New Showtimes" in text
    assert "City Centre Almaza" in text and "Mall of Egypt" in text
    assert "December 16, 2026" in text and "December 17, 2026" in text
    assert "Standard" in text and "IMAX" in text and "https://" in text


def test_run_movie_watch_parses_discovery_date_without_refetch(tmp_path):
    watch = _movie_watch(tmp_path)
    pages = _movie_pages()
    alerts, proposed, failures = run_watch(watch, _fixture_fetch(pages), {"notified_for": {}}, MOVIE_NOW,
                                 sleep=lambda _: None)
    assert not failures
    assert len(proposed["movie_alerted"][watch.id]) == 82
    assert "82 New Showtimes" in alerts[0].texts["telegram"]


@pytest.mark.parametrize("kind", ["date", "movie"])
def test_run_watch_escapes_discord_labels_and_preserves_links(tmp_path, kind):
    if kind == "movie":
        watch = _movie_watch(tmp_path)
        pages = {url: _read(name) for url, name in _movie_pages().items()}
    else:
        watch = Watch(id="escape", site="vox", movie_slug="m", cinema_slug="city-centre-almaza",
                      timezone="Africa/Cairo", target_date=date(2026, 12, 16))
        pages = {vox_url(watch, watch.target_date): _read("vox_movie.html")}
    # Derive labels from the saved capture; keep booking links and markup intact.
    pages = {url: html.replace("Avengers: Doomsday (Infinity Vision)", "Movie *bold* [link]")
             .replace("City Centre Alexandria", "Cinema _name_")
             .replace("<strong>MAX</strong>", "<strong>MAX *label*</strong>")
             for url, html in pages.items()}
    alerts, _, failures = run_watch(watch, lambda url, headers: pages[url], {"notified_for": {}}, MOVIE_NOW,
                          sleep=lambda _: None)
    assert not failures
    text = alerts[0].texts["discord"]
    assert r"Movie \*bold\* \[link\]" in text
    assert r"Cinema \_name\_" in text
    assert r"**MAX \*label\***" in text
    assert "](https://egy.voxcinemas.com/booking/0032-169550)" in text


@pytest.mark.parametrize("html,warning", [("<div>changed calendar</div>", True),
                                          pytest.param(_read("scene_with_showtimes.html"), True, id="showtimes-without-calendar"),
                                          ("", False), ("<p>No showtimes could be found</p>", False)])
def test_run_scene_discovery_warns_without_calendar_or_empty_marker(tmp_path, caplog, html, warning):
    watch = _movie_watch(tmp_path, movie={"scene": ["resident_evil"]}, cinemas=["district-5"])
    pages = {"https://district5.scenecinemas.com/movie-details/resident_evil.html": html}
    state = {"notified_for": {}}
    assert run_watch(watch, lambda url, headers: pages[url], state, SCENE_MOVIE_NOW,
                     sleep=lambda _: None) == ([], state, ())
    assert ("no showtimes AND no expected empty-state marker" in caplog.text) is warning


@pytest.mark.parametrize("cinemas,count", [("all", 82), (["city-centre-alexandria"], 0)])
def test_run_movie_watch_warns_for_unknown_cinema_heading(tmp_path, caplog, cinemas, count):
    watch = _movie_watch(tmp_path, cinemas=cinemas)
    pages = {url: _read(name).replace("City Centre Alexandria", "Renamed Cinema")
             for url, name in _movie_pages().items()}
    state = {"notified_for": {}}
    alerts, proposed, failures = run_watch(watch, lambda url, headers: pages[url], state, MOVIE_NOW,
                                 sleep=lambda _: None)
    assert not failures
    assert "Renamed Cinema" in caplog.text and "page structure may have changed" in caplog.text
    if count:
        assert len(proposed["movie_alerted"][watch.id]) == count
    else:
        assert (alerts, proposed) == ([], state)


def test_run_movie_watch_paces_every_fetch_without_initial_pause(tmp_path):
    watch = _movie_watch(tmp_path, movie={"scene": ["resident_evil"],
                                        "vox": ["avengers-doomsday-infinity-vision"]})
    pages = {**_scene_movie_pages(), **_movie_pages()}
    events = []
    fixture_fetch = _fixture_fetch(pages)

    def fetch(url, headers):
        events.append("fetch")
        return fixture_fetch(url, headers)
    run_watch(watch, fetch, {"notified_for": {}}, SCENE_MOVIE_NOW,
              sleep=lambda seconds: events.append(seconds))
    assert events == ["fetch"] + [1, "fetch"] * 13
