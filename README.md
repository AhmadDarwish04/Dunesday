# Cinema Showtime Notifier

Get a Telegram or Discord Alert the moment a movie's Showtimes become Published or Bookable — so you can book before it sells out.

Checks every 15 minutes via GitHub Actions. Date watches send one Alert per date; Movie watches send an Alert for each batch of New Showtimes.

Works with **VOX Cinemas Egypt**, **Scene Cinemas** (District 5 and Cairo Festival City), or both at once.

---

## Setup (once)

### Step 1 — Create a Telegram bot

1. Open Telegram and message [**@BotFather**](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts.
3. Copy the **bot token** it gives you (looks like `1234567:AAE...`).

> Keep this token private. Anyone who has it can post as your bot.

### Step 2 — Get your chat ID

1. Open your new bot's chat and send it any message (e.g. `hi`).
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser.
3. Find `"chat":{"id":123456789` — that number is your **chat ID**.

Or run `python get_chat_id.py` (with `TELEGRAM_BOT_TOKEN` set), which lists the candidates for you.

> **Want a friend to get the alerts too?** Create a Telegram group and add your bot to it, then send **`/start@your_bot_name`** in the group — bots ignore ordinary group chatter by default, so a plain "hi" won't show up in `getUpdates`. Use the group's ID (it starts with `-`). Everyone in the group gets notified.

### Step 3 — Add your secrets to GitHub

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | The token from Step 1 |
| `TELEGRAM_CHAT_ID` | The ID from Step 2 |
| `DISCORD_WEBHOOK_URL` | The webhook URL for your Discord channel (Channel Settings → Integrations → Webhooks) |

Only the secrets for Channels used by your Watches are required. Watches default to both
Telegram and Discord; set `"channels": ["telegram"]` or `"channels": ["discord"]` to use one.
Discord-only setups can skip the Telegram steps above.

Optionally, under the **Variables** tab, add `TIMEZONE` (e.g. `Africa/Cairo`). Defaults to `Africa/Cairo`.

### Step 4 — Turn on Actions

**Settings → Actions → General:**

- Allow all actions
- Set **Workflow permissions** to **Read and write** ← required, or the bot can't remember what it already told you

### Step 5 — Choose what to watch

Edit [`watches.json`](watches.json) and commit — pick the recipe you need from
[What to put in `watches.json`](#what-to-put-in-watchesjson) below.

That's everything. It's now running.

### Step 6 — *(optional)* Make the timing precise

GitHub's scheduler is best-effort: it can drift 10–60 minutes when Actions is busy, and sometimes
skips a check entirely. Fine for "tell me when the schedule is out", frustrating for "tell me the
second seats go on sale".

If you want 15 minutes to actually mean 15 minutes, deploy the small Cloudflare Worker in
[`scheduler/`](scheduler/) — it presses the button on time instead. Free, ~5 minutes to set up, and
it changes nothing about how the checks themselves work. Instructions: [`scheduler/README.md`](scheduler/README.md).

---

## What to put in `watches.json`

### Seat watch (VOX)

A booking link on VOX does not guarantee free seats. This watch reads the seat map
and alerts when at least two Premium seats are free (they need not be adjacent).
It alerts once while the threshold holds, then re-arms when fewer seats are free.

```json
{
  "id": "avengers-almaza-premium",
  "kind": "seat",
  "site": "vox",
  "movie": {
    "vox": [
      "avengers-doomsday",
      "avengers-doomsday-infinity-vision"
    ]
  },
  "match": [
    "doomsday"
  ],
  "cinema_slug": "city-centre-almaza",
  "dates": {
    "from": "2026-12-16",
    "to": "2026-12-18"
  },
  "from": "18:00",
  "category": "Premium",
  "min_free": 2,
  "channels": [
    "telegram",
    "discord"
  ]
}
```

Dates are inclusive; `from` is the earliest Showtime start in 24-hour local time.
Seat categories ignore case; `Regular` maps to VOX's `standard` seats. Movie slugs
and/or `match` use the same discovery as Movie watches. Only currently Bookable
Showtimes at the selected Cinema are checked. Alerts include up to ten free seat
labels and the Showtime booking link. Channels and Discord mentions work as above.

### 🅰️ VOX only

```json
{
  "watches": [
    {
      "id": "spiderman-almaza-aug6",
      "site": "vox",
      "movie_slug": "spider-man-brand-new-day",
      "cinema_slug": "city-centre-almaza",
      "target": { "date": "2026-08-06" },
      "notify_on": "bookable"
    }
  ]
}
```

### 🅱️ Scene only

```json
{
  "watches": [
    {
      "id": "odyssey-d5-friday",
      "site": "scene",
      "movie_slug": "the-odyssey",
      "target": { "weekday": "friday" }
    }
  ]
}
```

Scene needs no `cinema_slug` — it's the single District 5 venue.

### 🆎 Both at once

```json
{
  "watches": [
    {
      "id": "odyssey-d5-friday",
      "site": "scene",
      "movie_slug": "the-odyssey",
      "target": { "weekday": "friday" }
    },
    {
      "id": "spiderman-almaza-aug6",
      "site": "vox",
      "movie_slug": "spider-man-brand-new-day",
      "cinema_slug": "city-centre-almaza",
      "target": { "date": "2026-08-06" },
      "notify_on": "bookable"
    }
  ]
}
```

Add as many as you like. Each one is checked separately, gets its own message, and can't break the others if its site goes down.

---

## Movie watches

```json
{
  "watches": [{
    "id": "avengers-doomsday",
    "movie": {
      "vox": ["avengers-doomsday", "avengers-doomsday-infinity-vision"],
      "scene": []
    },
    "match": ["doomsday"],
    "cinemas": "all",
    "until": "2027-01-31",
    "channels": ["telegram", "discord"],
    "mention": ["123456789"]
  }]
}
```

A Movie watch discovers dates and Cinemas and Alerts on each New Showtime: a Bookable Showtime
it has not alerted about before. The first run includes already Bookable Showtimes. Use `"all"` or
a list of Cinema slugs (for example `["city-centre-almaza", "district-5"]`); an empty Site slug
list uses Movie match discovery when keywords apply to that Site. Set `"match": ["doomsday"]`
for every supported Site, or `"match": {"scene": ["doomsday"]}` for selected Sites. Keep `movie`
as a map of slug lists; match-only Watches can use `"movie": {}`. A Movie watch needs at least
one explicit slug or Movie match. Keywords must be non-empty strings in a non-empty list.
Every keyword must occur in the slug or title, case-insensitively; hyphens, underscores and
spaces are equivalent. `doomsday` finds both Doomsday releases; `avengers` also finds Endgame
Encore, so choose the more specific keyword.

When a matched movie is first Listed, a short heads-up Alert links to its movie page, even
before Showtimes exist. This happens once per Scene Cinema and once site-wide for VOX;
explicit slugs never produce heads-up Alerts. `until` is optional and stops the Watch after
that date. Movie watches wait for Bookable Showtimes before sending Showtime Alerts; `target` and `notify_on` belong to
Date watches. Both kinds accept `channels`, `mention`, and `timezone`.

---

## Finding the slugs

**VOX** — open the movie's showtimes page and read the URL:

```
https://egy.voxcinemas.com/showtimes?c=city-centre-almaza&m=spider-man-brand-new-day&d=20260804
                                       └── cinema_slug ──┘   └──── movie_slug ─────┘
```

**Scene** — open the movie page and read the URL:

```
https://district5.scenecinemas.com/movie-details/the-odyssey.html
                                                 └ movie_slug ┘
```

---

## Options

| Field | Required? | What it does |
| --- | --- | --- |
| `id` | ✅ | Any unique name. Used to remember what's been sent — **rename it to re-send.** |
| `site` | ✅ | `vox` or `scene` |
| `movie_slug` | ✅ | From the URL (see above) |
| `cinema_slug` | VOX only | From the URL (see above) |
| `target` | ✅ | Pick **one**: `{"date": "2026-08-06"}` or `{"weekday": "friday"}` |
| `notify_on` | optional | `published` (default) or `bookable` — see below |
| `channels` | optional | Non-empty list of `telegram` and/or `discord`; defaults to both |
| `mention` | optional | List of numeric Discord user ID strings, e.g. `["123456789"]`; defaults to empty |
| `timezone` | optional | e.g. `Europe/London`. Defaults to your `TIMEZONE` variable |
| `base_url` | advanced | Scene only — point at a different Scene branch's movie-details URL |

### `target`: fixed date vs. weekday

- `{"date": "2026-08-06"}` — one specific day. Stops checking once it passes.
- `{"weekday": "friday"}` — always the *next* Friday. Rolls forward forever.

### `notify_on`: published vs. bookable

Both sites list showtimes *before* tickets go on sale, so choose when you want the ping:

- **`published`** (default) — "the schedule is out" — tells you the times as soon as they appear.
- **`bookable`** — "I can buy tickets now" — waits until a showtime is actually purchasable.

Use `bookable` if your goal is to grab seats. It keeps checking until sales open.

Discord Alerts mention only the listed users; role and everyone mentions are disabled.
Long Discord Alerts split into messages of at most 2000 characters. If any Channel fails,
the Watch retries next run, so a Channel that already succeeded may receive the Alert again.

If you get something wrong (unknown site, both target types, a duplicate `id`, a bad date), the run fails immediately with a clear message rather than quietly never notifying you.

---

## Test it

**Actions → Test Telegram → Run workflow.**

Sends one sample Alert per Watch to its real Channels, including Discord Mentions, each marked with a 🧪 TEST banner. Use this to confirm your credentials and formatting work — no need to wait for real showtimes.

To check the real thing is running: **Actions → Check Showtimes → Run workflow**, then read the log. You'll see one line per watch, e.g.:

```
[odyssey-d5-friday] No showtimes yet for 2026-07-31
```

---

## Re-sending an alert

`state.json` remembers what's already been sent:

```json
{ "notified_for": { "odyssey-d5-friday": "20260731" } }
```

Movie watch state is stored under `movie_alerted` as Showtime identities and dates; past dates are pruned. Listed heads-up identities are stored under `movie_listed` per Watch and kept indefinitely. Both are saved only after all Alerts for the Watch are delivered. To re-arm a Watch, delete its entry from the appropriate section and commit, or rename its id. Deleting the whole file resets everything.

---

## Running it on your own machine

```bash
pip install -r requirements.txt pytest
pytest -q                     # run the tests

# Set the credentials for your Watches' Channels first (the sample helper also reads .env)
# Telegram: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID; Discord: DISCORD_WEBHOOK_URL
python check_showtimes.py     # ⚠️ really sends messages and updates state.json
python send_test_message.py   # sample alert per watch
```

To try out a config without touching the production one, point `WATCHES_FILE` at a scratch file:

```bash
WATCHES_FILE=my-test.json python check_showtimes.py     # macOS / Linux
```

```powershell
$env:WATCHES_FILE = "my-test.json"; python check_showtimes.py   # Windows PowerShell
```

---

## Troubleshooting

**Nothing ever arrives.** Check **Actions** for failed runs. If runs are green but silent, the date genuinely isn't published yet — the log says so per watch. Both sites only publish a limited window ahead, often just a few days.

**It stopped working after I made the Telegram group bigger.** When Telegram upgrades a group to a "supergroup" its chat ID changes (it becomes `-100…`). Redo Step 2 and update `TELEGRAM_CHAT_ID`.

**The workflow can't push `state.json`.** Workflow permissions aren't set to **Read and write** (Step 4).

**Times show as "not bookable" instead of "sold out" on VOX.** That's deliberate — VOX uses one label for both "sold out" and "not on sale yet", so claiming "sold out" would be wrong. Scene reports real sell-outs and says `sold out`.

---

## Notes

- GitHub's own scheduler is best-effort and can drift 10–60 minutes, or skip a firing entirely — see [Step 6](#step-6--optional-make-the-timing-precise) if that matters to you.
- No headless browser — showtimes come from each site's own endpoints, via `curl_cffi` with Chrome TLS impersonation (both sites reject plain `requests`).
- If a site redesigns its pages, the run exits cleanly as "nothing yet" instead of crashing. VOX would be silent about it, so the script logs a warning when a page has neither showtimes nor the expected "no showtimes" notice.

For architecture and development notes, see [`CLAUDE.md`](CLAUDE.md).
