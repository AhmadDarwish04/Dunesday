# Cinema Showtime Notifier

Watches Egyptian cinema chains' websites and alerts a group of friends when showtimes for movies they care about appear or open for booking.

## Where movies play

**Site**:
A cinema chain's booking website, which may serve several cinemas (e.g. VOX Egypt, Scene Cinemas).
_Avoid_: Chain, provider, source

**Cinema**:
One physical location of a Site (e.g. City Centre Almaza, Scene District 5, Scene Cairo Festival City).
_Avoid_: Branch, theater, venue, location

**Movie slug**:
A Site's own identifier for a movie. One film may have several slugs on one Site (e.g. a standard and an "Infinity Vision" release), and slugs differ between Sites.
_Avoid_: Movie ID, film code

**Listed**:
A movie is listed on a Site once it appears in that Site's movie listings (now showing or coming soon), which can happen before any of its Showtimes are Published.
_Avoid_: Announced, added

**Movie match**:
Keywords a Movie watch uses to find a movie's Movie slugs on each Site by itself; a slug matches when its slug or title contains every keyword.
_Avoid_: Search, filter

**Seat category**:
The price tier a seat is sold under within a Showtime's seat map (e.g. VOX "Premium" vs "Regular").
_Avoid_: Seat type, class, zone

**Seat watch**:
A Watch on specific Showtimes' seat maps that alerts when at least a given number of seats in a Seat category become free, and re-arms once they are taken again.
_Avoid_: Seat tracker, availability watch

**Experience**:
The screen format a Showtime is sold under (Standard, IMAX, GOLD, 4DX, VIP).
_Avoid_: Format, hall type

## Showtimes

**Showtime**:
One screening of a movie: a Cinema, a date, a time and an Experience.
_Avoid_: Slot, session, screening

**Published**:
A Showtime is published once it appears on its Site, whether or not tickets can be bought yet.
_Avoid_: Live, released

**Bookable**:
A Published Showtime that tickets can be bought for right now.
_Avoid_: Available, on sale

**Not bookable**:
VOX's single state for a Published Showtime that cannot be bought, covering both "sold out" and "not on sale yet". Distinct from **Sold out**.
_Avoid_: Unavailable

**Sold out**:
Scene's explicit state for a Showtime with no tickets left.

**New Showtime**:
A Bookable Showtime the Watch has never alerted about before. A Showtime that flips from Not bookable to Bookable is new the first time it does so; later flips back and forth never re-alert.
_Avoid_: New slot, fresh showtime

## Watching

**Watch**:
A standing request to be alerted about a movie, defined in the repo's watch config and identified by its id.
_Avoid_: Subscription, tracker, job

**Date watch**:
A Watch on one movie at one Cinema for one date (fixed, or the next given weekday), alerting once when that date's Showtimes are Published or Bookable.

**Movie watch**:
A Watch on one movie across a set of Cinemas (or all Cinemas) and every future date, alerting on each New Showtime.
_Avoid_: Open watch, rolling watch

**Alert**:
A message sent to one or more Channels when a Watch fires.
_Avoid_: Notification, ping

**Channel**:
Where Alerts are delivered: the Telegram group or the Discord channel. A Discord Alert can mention specific friends.
_Avoid_: Destination, target, sink
