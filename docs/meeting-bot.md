# Meeting bot

`localscribe record` captures calls you are in, from your own Mac. The bot is the
other way round: it runs in a container, watches your organization's calendars,
joins each Google Meet as its own participant, and files the summary in Google
Drive as a Doc.

```
Calendars ─► scheduler ─► one Chromium per call, signed in as the bot
                             │ audio: private PulseAudio sink ─► WAV
                             │ names: Meet captions ─► speaker timeline
                             ▼
                          Whisper ─► transcript ─► summary ─► Drive: root/YYYY/MM/
```

Audio is deleted once the note is safely in Drive. If filing fails the audio stays
on disk and is retried the next time the bot restarts.

## What it joins

Every event with a Meet link and at least two people, except:

- events tagged `[no-notes]` in the title or description
- private or confidential events
- cancelled and all-day events
- external meetings or 1:1s, only if you set `SKIP_EXTERNAL` or `SKIP_ONE_ON_ONE`

The only URLs it will open are `https://meet.google.com/xxx-xxxx-xxx`, because
invites can come from anyone.

## Who can read the notes

Each Doc is shared, without an email, with the colleagues who were invited.
External guests get nothing. **Everyone with access to the root folder can also
read every note**, and in a shared drive that means every member of the drive, so
choose a folder with a small membership.

## Setup

You need a Google Workspace admin for steps 1 and 2.

1. **Create a user for the bot**, e.g. `notetaker@yourdomain`. Give it a name people
   will recognize, since that is what appears in the call.
2. **Create a service account** with a JSON key and grant it domain-wide delegation
   in the Admin console for these scopes:
   ```
   https://www.googleapis.com/auth/calendar.events.readonly
   https://www.googleapis.com/auth/admin.directory.user.readonly
   https://www.googleapis.com/auth/drive
   ```
   This key can read any user's calendar and act in Drive as any user. Treat it like
   a production secret and keep it out of git.
3. **Create the Drive folder** and give the bot user Contributor (or Editor) access.
4. **Configure it**:
   ```bash
   cp .env.bot.example .env.bot      # then fill it in
   mkdir -p secrets && cp key.json secrets/service-account.json
   docker compose -f docker-compose.bot.yml build
   ```
5. **Sign the bot in, once.** Open <http://localhost:6080/vnc.html>, sign in, and
   close the browser window:
   ```bash
   docker compose -f docker-compose.bot.yml run --rm --service-ports bot login
   ```
6. **Try one real call before turning it loose.** Start a Meet with a colleague:
   ```bash
   docker compose -f docker-compose.bot.yml run --rm bot join https://meet.google.com/xxx-xxxx-xxx \
     --title "Bot test" --minutes 10 --share you@yourdomain
   ```
7. **Run it**:
   ```bash
   docker compose -f docker-compose.bot.yml up -d
   docker compose -f docker-compose.bot.yml run --rm bot status
   ```

## Getting in without being let in

Google may hold bots at the door or flag them for approval. The most reliable route
is a signed-in bot that is on the invite, but the scheduler does not add it to
events. Meetings whose access type requires knocking will wait up to
`ADMIT_TIMEOUT_SECONDS` for someone to admit it, then record `no_show`.

## Consent

The bot is visible in the call and posts `NOTICE` in the chat when it arrives. If the
chat post fails it logs a warning and keeps recording, with its name in the
participant list as the only signal. Whether that is enough depends on where people
are, and some places require everyone's consent, so check before pointing it at
meetings with outside guests. `SKIP_EXTERNAL=1` leaves those out.

## When it breaks

Meet's UI is not an API. All the wording and labels the bot relies on are in
`localscribe/bot/meet.py`, and the usual symptoms are:

| Symptom | Likely cause |
|---|---|
| Every meeting ends `no_show` | Join button or sign-in expired: run `login` again |
| Transcript says `Speaker:` throughout | Captions button or caption layout changed |
| Bot stays until the hard stop | People-count label changed, so it can't tell it's alone |

## Testing

`pytest` covers the decisions with no browser or account. `tests/e2e/` runs the real
image, with real Chromium, PulseAudio and Whisper, against a fake Meet page and a fake
Drive; instructions are at the top of `run_fake_meet.py`.
