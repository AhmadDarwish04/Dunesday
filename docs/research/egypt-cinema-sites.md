# Egyptian cinema Sites: candidates for new adapters

Research date: **2026-10-27** (US time; Cairo was already 2026-10-28 morning during most probes).
All fetches were made with `curl_cffi` (`impersonate="chrome124"`), the same transport `fetch_page` uses.
"Verified" means the URL was fetched and the claim checked against the response on 2026-10-27.
Anything not checked that way is marked **UNVERIFIED**.

Already covered, so not researched here: VOX Egypt (`egy.voxcinemas.com`, 3 cinemas) and Scene
(`district5.scenecinemas.com`, `cfc.scenecinemas.com`).

## Summary (ranked by cinemas covered × ease)

| Rank | Candidate | Cinemas | Delivery | Bookable signal | Stable showtime id | curl_cffi | Difficulty |
|---|---|---|---|---|---|---|---|
| 1 | **elCinema booking API** (`elcinema.com/en/booking/...`) | ~12 physical (14 listings): all Premiere cinemas, Galaxy Maadi, Sun City, Zamalek, El Tahrir, Grand Hurghada, Zawya, Plaza CineComfort | XHR JSON (CSRF + session cookie) | Yes. It lists only times currently on sale | `ss_id` for Premiere-sourced cinemas; none for others | 200 | Medium |
| 2 | **Premiere Cinemas** (`premiere-cinemas.com`) | 5 (Nile City, Americana Plaza, Point90, Arkan, Galaxy Manial) | Next.js RSC in HTML for movie lists + `show_dates`; times via encrypted JSON API | Dates only (from RSC). The times API is encrypted | `movie_unique_identifier` per movie (stable over the test) | 200 | Easy for "date published"; hard for times |
| 3 | **Galaxy Cinemas Sohag** (`sohag.galaxy-cinema.com`) | 1 | Same platform as Scene: XHR `?business_day=DD-MM-YYYY&ajax=1` | Same markup as Scene (`ex_*`, showtime links) | Yes: `/showtime-<24 hex>` | 200 | Easy (reuse Scene adapter) |
| 4 | **elCinema theater pages** (`elcinema.com/en/theater/<id>/?date=`) | 122 Egypt listings | Server HTML | No (times and prices only) | No | 200 | Easy, but data **diverged from VOX** (see below) |
| 5 | **Zawya** (`zawyacinema.com`) | 1 | Server HTML (ASP.NET) | No state seen. Booking links go to elCinema | Film id only (`/en/Films/Details/<id>`) | 200 | Easy, low value |
| — | Filbalad, Tazkarti, TicketsMarché, Novo, Cinema City, Renaissance, Stars, Golden Stars, Odeon, CineGold, Mall of Arabia | — | — | — | — | — | Not feasible / not found (see below) |

## Candidates

### 1. elCinema as a booking platform (partner cinemas)

elCinema sells tickets for a set of partner cinemas through its own booking flow. That gives a real
**Bookable** signal, which plain listings don't.

- Partner list: the Egypt theater index marks partners with booking links (`/en/booking/<work_id>/<theater_id>`).
  Partners on 2026-10-27: Premiere Point 90 (3101219), Point 90 MX4D (3101261), Premiere Americana
  (3101133), Americana MX4D (3101260), Arkan (3101401), Arkan VIP (3101402), Zawya (3101183), Galaxy El Maadi
  (3101080), Galaxy El Maadi Dine-in (3101576), Galaxy El Maadi VIP (3101411), Sun City (3101119), Sun
  City VIP (3101257), Plaza CineComfort (3101136), Zamalek (3101262), Galaxy El Manyal (3101038), Grand
  Hurghada (3101334), Premiere Nile (3101020), Premiere Nile VIP (3101633), El Tahrir (3101618).
  Verified at `https://elcinema.com/en/theater/1/?order=rating&page=1..13` (2026-10-27).
- Flow, from `cdn.elcinema.com/assets/bookings-ca585b21….js` (2026-10-27):
  1. `GET https://elcinema.com/en/booking/<work_id>/<theater_id>`. This sets the session cookie and the
     `<meta name="csrf-token">`.
  2. `POST /en/booking/ajax_phases` with `move=refresh` and `serialized=<form#defaults>`. It returns HTML whose
     `#dropdown-multiplex` lists every partner cinema showing that work, each with `data-source` (`2` =
     Premiere back end, `1` = other).
  3. `POST /en/booking/ajax_dropdowns` with `data[work_id]`, `data[theater_id]`, `data[type]=dates|times`,
     `data[date]`, and `data[source]`, plus the `X-CSRF-Token` header. It returns JSON.
     - dates: `[{"value":"2026-10-29","text":"Thursday 29 October"}, …]`
     - times (source 2, Premiere Point 90, Wildwood): `{"value":"11:00","category":"2D","data":{"md_id":7435,"ss_id":16901}}`
     - times (source 1, Zawya, Wildwood): `{"value":"2026-10-29 13:00:00","free_seating":true,"subtitle":"Subtitle: Arabic ",…}` with no id
  All of the above was verified 2026-10-27 for work 2088495 (Wildwood) at 3101219 and 3101183, and for work 2099773 at Sun City
  3101119.
- Date window: Premiere-sourced cinemas offered 6 dates (2026-10-29 to 11-03). Sun City offered only
  2026-10-28, so the window varies by back end. Dates appear to start when sales open, so presence in this list
  ≈ Bookable. Whether a sold-out time disappears or is flagged is **UNVERIFIED** (seat-level data needs
  the `ajax_seat_change` / seat-layout step, which was not exercised).
- Enumeration: elCinema `work_id`s act as the Movie slug. They appear on `/en/now/`, `/en/soon/` and
  `/en/available_for_booking/` (verified 200). Theater ids are stable numeric ids.
- Fetchability: 200 with curl_cffi. Session plus CSRF is needed, but no login.
- Difficulty: **medium**. It needs a 3-request session flow and per-back-end JSON shapes, and one `work_id` covers
  many cinemas.

### 2. Premiere Cinemas (`https://www.premiere-cinemas.com/en`)

- Live and verified 200 on 2026-10-27. It is a Next.js app-router site behind Cloudflare.
- Cinemas (from the RSC `cinemaData` on the home page): 1 Nile Cinema (Nile City, 8 screens), 2 Premiere Plaza
  (Americana Plaza, Sheikh Zayed, 14), 3 Galaxy elManial (6), 4 Premiere Point90 (11), 5 Cima Arkan (9).
  So "Galaxy Manial", "Americana Plaza cinema", "Nile City" and "Arkan" are all Premiere.
- Movies: each `now-showing` entry in the RSC payload carries `movie_unique_identifier` (e.g. `1001.51` =
  The Odyssey, `4848.932` = Sense and Sensibility), `movie_id`, `schdeuled_cinema_list` (e.g. `"1,2,3,4,5"`)
  and `show_dates` (e.g. `"2026-10-29,…,2026-11-05"`). Two fetches 3 s apart returned identical id→title
  maps for all 21 movies (`https://www.premiere-cinemas.com/en/now-showing`, 2026-10-27). Long-term
  stability is **UNVERIFIED**.
- Movie page: `https://www.premiere-cinemas.com/en/movie-details/<movie_unique_identifier>` returns details plus
  similar movies with `show_dates`, but **no times** in the HTML.
- Times: loaded client-side from `https://mifcentral-api.premiere-cinemas.com` (`/api/external/quick-book`,
  `/api/cinema/schedule-details/<ss_id>`, `/api/external/seat-layout`). An axios interceptor wraps requests
  as `{encryptedText: …}` and decrypts `encryptedData` responses. A static JWT and `x-web-security` header
  are embedded in the bundle (`/_next/static/chunks/…`, 2026-10-27). Reproducing that means
  reimplementing their client crypto, which is brittle.
- Signals: `show_dates` × `schdeuled_cinema_list` is an easy **Published (per movie, per date)** signal, but
  it isn't per cinema per date. Per-time bookability is available more cheaply via elCinema (#1, source 2), which
  exposes Premiere's `ss_id`.
- `robots.txt`: 404 (`https://www.premiere-cinemas.com/robots.txt` → `/en/robots.txt` 404).
- Difficulty: **easy** for a dates-level Published watch, and **hard** for times.

### 3. Galaxy Cinemas Sohag (`https://sohag.galaxy-cinema.com/`)

- Live, 200. It has 1 cinema, "Galaxy Mega – DownTown Mall, New Sohag" (`/branches.html`, verified 2026-10-27).
- Same white-label platform as Scene (`customers/login_bar`, `movie-details/<slug>.html`). The Scene XHR works
  unchanged: `https://sohag.galaxy-cinema.com/movie-details/clayface.html?business_day=28-10-2026&ajax=1`
  returned 6 times under `span.ex_stand`, each linking to `/showtime-<24-hex id>` (verified 2026-10-27).
- So adding it to the existing Scene adapter means only a new base URL and cinema name. Its sold-out markup
  wasn't observed but is presumably identical (**UNVERIFIED**).
- `robots.txt` disallows all agents except Googlebot/AdsBot (`https://sohag.galaxy-cinema.com/robots.txt`).
  Scene's own `scenecinemas.com/robots.txt` is `Allow: /`.
- Note: `https://galaxy-cinema.com/` is a meta-refresh to `https://scenecinemas.com`, and `scenecinemas.com` lists
  only the district5 and cfc subdomains (verified 2026-10-27). No other Galaxy subdomains were found (`maadi.`, `manial.` → NXDOMAIN).
  Galaxy Maadi/Manial are separate operators (Manial = Premiere, Maadi = elCinema partner).
- Difficulty: **easy**, but the value is low (Sohag).

### 4. elCinema theater pages (aggregator)

- Index: `https://elcinema.com/en/theater/1/?order=rating&page=N` (13 pages) lists **122 Egypt
  cinemas**, including VOX (Almaza 3101353, Mall of Egypt 3101271, City Centre Alexandria 3101027), Scene (D5
  3101536/3101537/3101567, CFC 3101258/3101259), City Stars 3101050, Galaxy Cineplex Mall of Arabia
  3101132, Renaissance (Smouha 3101175, Masria Plaza 3101131), Golden Stars, Sea Cinema, and the Alshab state chain.
- Showtimes: `GET https://elcinema.com/en/theater/<id>/?date=YYYY-MM-DD` renders that date server-side
  (also `POST /en/theater/ajax_show` with `date`, `id` and CSRF). It shows 7 dates (2026-10-27 to 11-02), with each
  movie → Experience (`<h6>`) → `table.showtimes tr` → time + price. There's no bookable or sold-out state and
  no showtime ids. Rows with `class="hide"` are just collapsed "More (n)" rows.
- **Freshness/accuracy problem:** for VOX City Centre Almaza on 2026-10-28, elCinema listed Resident Evil
  Standard 11:15, 16:15, 18:30, 23:30 and IMAX 16:45. VOX itself listed IMAX 13:00 and Standard 16:15, 23:30
  (`https://egy.voxcinemas.com/showtimes?c=city-centre-almaza&m=resident-evil&d=20261028`). For 2026-10-29
  elCinema listed Resident Evil times while VOX said "No showtimes". Sense and Sensibility (watched in
  `watches.json`) did not appear on elCinema's Almaza 10-29 page. So for non-partner cinemas the listings
  look **projected or manually entered**, and they'd produce false "Published" alerts.
- robots.txt: `User-agent: * Allow: /` (bingbot crawl-delay 10) at `https://elcinema.com/robots.txt`.
  Terms (`https://elcinema.com/en/copyrights/`) allow copying for **non-commercial use** with attribution,
  and commercial use needs written consent. A private friends-group notifier is plausibly non-commercial, but
  that's a judgement call, not legal advice.
- Difficulty: **easy** to parse. It's not trustworthy as a Published signal for chains with their own site.

### 5. Zawya (`https://zawyacinema.com/`)

- Live, 200 (ASP.NET/IIS). One cinema (Cinema Karim, 15 Emad El Deen, Downtown).
- Film page `https://zawyacinema.com/en/Films/Details/1352` (No Paradise If You Are Killed by a Woman) lists
  server-rendered screenings (`Tue 27.10.2026 19:00`, `21:30`). Each one has an empty
  `p.screenings-bottom_state` (probably a sold-out slot, **UNVERIFIED**) and a "Book ticket" link to
  `https://elcinema.com/work/2101935/`. So Zawya bookability comes from elCinema (#1, source 1).
- Enumeration: `/nowshowing`, `/films/FCommingSoon`, `/calendar`. Film ids are numeric.
- `robots.txt` is 404. Difficulty: **easy**, single art-house cinema.

### Not feasible / not found (all checked 2026-10-27)

- **Cinema City / Point 90**: `cinemacity.com.eg` and `cinemacityegypt.com` don't resolve (NXDOMAIN). Point 90 is Premiere (#2).
- **Galaxy Cinema (Cairo)**: `galaxycinema.com.eg` doesn't resolve. `galaxy-cinema.com` redirects to Scene (#3).
- **Renaissance**: `renaissancecinemas.com`, `renaissance-cinema.com`, `renaissancecinema.com.eg` and `renaissancecinemas.com.eg` don't resolve. It's only on elCinema.
- **Stars Cinema (Citystars)**: `starscinema.com.eg`, `cinemastars.com.eg` and `starscinema.net` don't resolve. The mall site
  `https://www.citystars-heliopolis.com.eg/en` is live, but no cinema booking was checked (**UNVERIFIED**). It's on elCinema (3101050) without booking.
- **Golden Stars, Odeon, CineGold, Cinema Point, IMAX Cairo, Americana Plaza, Mall of Arabia**: the guessed domains
  (`goldenstars.com.eg`, `goldenstarscinema.com`, `odeoncinemaseg.com`, `odeoncinema.com.eg`, `cinegold.com.eg`,
  `cinemapoint.com.eg`, `imaxcairo.com`, `americanaplaza.com`, `mallofarabia.com.eg`, `galaxycineplex.com`) don't resolve.
  The official sites may exist under other names (**UNVERIFIED**). Americana Plaza's cinema is Premiere.
  Mall of Egypt is VOX (already covered).
- **Novo Cinemas**: `https://www.novocinemas.com/egypt` redirected to `qa.novocinemas.com/egypt` and returned 404. No Egypt presence was found.
- **Sun City**: `suncitycinema.com` is a parked "/lander" page. Booking is via elCinema (#1).
- **Arkan**: `https://arkanplaza.com/` is the mall site. Its cinema is Premiere's "Cima Arkan" (#2).
- **Filbalad** (`https://www.filbalad.com/ar/eg/Cinemas/Index`): live, but cinema pages have no showtimes, and the
  "now showing" list was stale (Michael, Egybest). Not usable.
- **Tazkarti** (`https://tazkarti.com/`): Angular SPA for events and sports. No cinema showtimes are found without JS (**UNVERIFIED** deeper).
- **TicketsMarché** (`https://www.ticketsmarche.com/`): categories are theatre, comedy, concerts, etc., with no cinema category.
- **El Gouna / Alexandria / North Coast**: only elCinema listings were found (Sea Cinema El Gouna 3101280, O New Alamein
  3101333, Renaissance Smouha, San Stefano, etc.). Filbalad lists "Cinevibes – El Alamein" without times. No own sites were found.

## Recommended next Sites

1. **elCinema booking adapter (`site: "elcinema-booking"`)**: one adapter covers about 12 partner cinemas (all 5 Premiere
   cinemas plus Sun City, Zamalek, Galaxy Maadi, El Tahrir, Grand Hurghada, Zawya, Plaza CineComfort) with a true
   Bookable signal. The Movie slug is the elCinema `work_id` and the Cinema is the elCinema theater id. Fit it to `notify_on: bookable`.
   Before building, capture fixtures for both `source` shapes and confirm how sold-out times appear.
2. **Galaxy Sohag via the existing Scene adapter**: a near-zero-cost config change, if anyone cares about Sohag.
   Note its robots.txt.
3. **Premiere dates-only adapter (optional)**: parse `show_dates` and `schdeuled_cinema_list` from the
   `now-showing` RSC as an early Published hint. Skip it if #1 is built.
4. Don't use elCinema *theater listings* as a Published source for VOX or Scene cinemas. They diverged from VOX.

Probe scripts are in the session scratchpad (`research-scripts/`), and raw HTML/JSON is in `research-html/`. Neither is committed.
