"""One-shot helper: send a sample Alert to each Watch's configured Channels.

Samples are derived from `watches.json`, so each message uses that watch's real site
wording ("sold out" vs "not bookable") and its real `notify_on` headline — the point is to
see what an actual alert will look like, not just to prove the bot token works.

Every message is prefixed with a TEST banner, because these land in a shared group.

Usage (PowerShell):
    $env:TELEGRAM_BOT_TOKEN = "..."
    $env:TELEGRAM_CHAT_ID   = "..."
    $env:DISCORD_WEBHOOK_URL = "..."
    python send_test_message.py                  # one message per watch
    python send_test_message.py odyssey-d5-friday  # only the named watch(es)

Exits 0 on success, 1 on any failure.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta

from check_showtimes import (
    SITES,
    CHANNELS,
    watch_defaults,
    Alert,
    MovieWatch,
    SeatWatch,
    seat_alert,
    Showtime,
    Watch,
    title_from_slug,
    build_alert,
    format_movie_message,
    load_config,
    load_watches,
    resolve_target_date,
    send_alert,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BANNER = "🧪 <b>TEST</b> — verifying the notifier. Not a real alert, please ignore."

log = logging.getLogger("send_test_message")


def sample_groups(watch: Watch) -> "dict[str, list[Showtime]]":
    """Fake showtimes shaped like the real thing: some bookable, some not."""
    if watch.site == "vox":
        booking = "https://egy.voxcinemas.com/booking/0047-sample"
        return {
            "Standard": [
                Showtime(time="1:45pm", href=f"{booking}1"),
                Showtime(time="5:00pm", href=f"{booking}2"),
            ],
            "GOLD": [
                Showtime(time="8:00pm", href="", soldout=True),
            ],
        }
    booking = "https://district5.scenecinemas.com/showtime-sample"
    return {
        "IMAX": [
            Showtime(time="04:00 PM", href=f"{booking}1"),
            Showtime(time="08:00 PM", href="", soldout=True),
        ],
        "Premiere": [
            Showtime(time="09:30 PM", href=f"{booking}2"),
        ],
    }


def sample_date(watch: Watch) -> date:
    """The watch's real target date, or a near-future stand-in if it's already passed."""
    target = resolve_target_date(watch)
    return target if target >= date.today() else date.today() + timedelta(days=3)



def sample_movie_alert(watch: MovieWatch, today: date) -> Alert:
    """Build a plausible Movie watch Alert with the production formatter."""
    booking = "https://egy.voxcinemas.com/booking/0047-sample"
    groups = {
        "City Centre Almaza": {
            today + timedelta(days=3): {
                "Standard": [Showtime(time="1:45pm", href=f"{booking}1"),
                             Showtime(time="5:00pm", href=f"{booking}2")],
                "GOLD": [Showtime(time="8:00pm", href=f"{booking}3")],
            },
        },
        "Mall of Egypt": {
            today + timedelta(days=4): {
                "IMAX": [Showtime(time="7:30pm", href=f"{booking}4")],
            },
        },
    }
    slug = next((slug for slugs in watch.movie.values() for slug in slugs), "")
    if not slug:
        slug = " ".join(next(iter(watch.match.values())))
    texts = {channel: CHANNELS[channel].mention_prefix(watch.mention) +
             format_movie_message(channel, title_from_slug(slug), groups, 4)
             for channel in watch.channels}
    return Alert(texts=texts, mentions=list(watch.mention))


def sample_seat_alert(watch: SeatWatch) -> Alert:
    """Use the real seat formatter with clearly synthetic seat labels/booking identity."""
    slug = next((slug for slugs in watch.movie.values() for slug in slugs), "")
    movie = title_from_slug(slug or " ".join(next(iter(watch.match.values()))))
    st = Showtime(time=watch.earliest, href="https://egy.voxcinemas.com/booking/0047-sample",
                  cinema=SITES[watch.site].cinema_names[watch.cinema_slug], experience="IMAX")
    labels = ["K-20", "K-19"] + [f"Sample-{i}" for i in range(3, watch.min_free + 1)]
    return seat_alert(watch, movie, watch.start_date, st, labels[:watch.min_free])


def main(argv: "list[str]") -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    path, timezone = watch_defaults(os.environ)
    watches = load_watches(path, timezone)
    wanted = set(argv)
    if wanted:
        unknown = wanted - {w.id for w in watches}
        if unknown:
            print(f"Unknown watch id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        watches = [w for w in watches if w.id in wanted]

    cfg = load_config(watches, os.environ)
    failures = []
    for watch in watches:
        if isinstance(watch, SeatWatch):
            alert = sample_seat_alert(watch)
        elif isinstance(watch, MovieWatch):
            alert = sample_movie_alert(watch, date.today())
        else:
            site = SITES[watch.site]
            headline = "is now bookable!" if watch.notify_on == "bookable" else "showtimes are live!"
            alert = build_alert(
                watch=watch,
                movie=title_from_slug(watch.movie_slug),
                cinema=(title_from_slug(watch.cinema_slug) if watch.cinema_slug
                        else site.default_cinema or watch.site.title()),
                target=sample_date(watch),
                groups=sample_groups(watch),
                soldout_label=site.soldout_label,
                headline=headline,
            )
        texts = {}
        for channel, text in alert.texts.items():
            banner = BANNER.replace("<b>TEST</b>", CHANNELS[channel].bold("TEST"))
            texts[channel] = banner + "\n\n" + text
        log.info("[%s] sending sample to %s", watch.id, ", ".join(texts))
        try:
            send_alert(cfg, Alert(texts=texts, mentions=alert.mentions))
        except Exception as exc:  # noqa: BLE001 — keep trying the other Watches
            log.error("[%s] failed: %s", watch.id, exc)
            failures.append(watch.id)

    log.info("%d sample Alert(s) sent — check the configured Channels", len(watches) - len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001
        logging.error("failed: %s", e)
        sys.exit(1)
