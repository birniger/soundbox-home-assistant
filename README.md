# Soundbox for Home Assistant

Soundbox brings a personal, adaptive internet-radio library to Home Assistant.
It is a media source and a set of actions for your existing speakers; it never
pretends to be a speaker itself.

## What it does

- Browse **All Stations**, **Favourites**, **Tags**, **Cities**, and **Radio
  Modes** from **Media** in the Home Assistant sidebar. Multiple connected
  accounts have separate folders. Choose a station or mode and a media player;
  the speaker fetches the stream itself.
- Play a named station or Smart, Morning, Night, Local, Favorites, Ordered, or
  Explore Radio through Media, the `soundbox.play` action, or Assist.
- Keep separate Soundbox sessions for separate rooms, so Next, More/Less, and
  Save Song act on the right speaker.
- Use Home Assistant Assist actions, including play, next, previous, more/less,
  and save-song. A ready-made English and German sentence pack is included.
- Choose whether to share listening activity to improve the same mode-specific
  learning used by Alexa and Soundbox's website. This is off by default.

## Requirements

- Home Assistant 2026.3.0 or newer.
- A free Soundbox account at [soundboxradio.com](https://soundboxradio.com).
  A demo library cannot be connected.
- The **My Home Assistant** integration, which Soundbox lists as a dependency
  and Home Assistant therefore loads for you. Soundbox signs you in through the
  `my.home-assistant.io` redirect, so a Home Assistant without it has no address
  this integration can be authorised against.

## Install

Add this repository to HACS as a custom repository, install **Soundbox**,
restart Home Assistant, then choose **Settings → Devices & services → Add
integration → Soundbox**. You sign in once at Soundbox; there is no API key
to copy and nothing to paste.

## Playback and actions

Starting a station or radio mode through Media, `soundbox.play`, or Assist
creates a session for the selected speaker. Next, Previous, More/Less and Save
Song use that session. Next and Previous stay in the selected radio mode;
after a direct station selection they follow station-bank order, as on Alexa.
Use Soundbox's actions or Assist commands for this navigation. A speaker's own
transport buttons depend on its media player integration.
More/Less teaches the current adaptive mode and the active Morning or Night
time profile. If neither applies, it teaches Smart Radio. It does not change
the station's manual preference setting or favourite membership.

| Action | Data |
| --- | --- |
| `soundbox.play` | `entity_id` and either `station_id` or `mode` |
| `soundbox.next`, `soundbox.previous` | `entity_id` |
| `soundbox.feedback` | `entity_id` and `value: more` or `value: less` |
| `soundbox.save_song` | `entity_id` |

For example, an automation can start Smart Radio with:

```yaml
action: soundbox.play
data:
  entity_id: media_player.kitchen
  mode: smart
```

Mode IDs are `smart`, `morning`, `night`, `local`, `favorites`, `ordered`, and
`discovery` (Explore Radio). When multiple accounts are connected, actions also
accept `config_entry_id`; Assist requires exactly one connected account.

Speaker controls become available when the selected player reports playback.
Stopping playback, changing to other audio when the player exposes its media
ID, or unloading the integration ends the local session. After a Home
Assistant restart, start playback through Soundbox again. Playback in a browser
without a selected Home Assistant media player has no speaker session.

## Listening activity

Open **Settings → Devices & services → Soundbox → Configure** and enable
**Share listening activity** for each account you want to contribute listening
data. Enabling or disabling this option reloads the integration; start playback
through Soundbox again afterwards.

When enabled, Soundbox receives playback start, resume, pause, progress, end and
skip events with estimated seconds spent in the player's `playing` state.
Progress is reported every five minutes while playing. Paused time is excluded,
and cumulative reports are counted incrementally rather than added repeatedly.
The estimates depend on the player's state and media ID reporting; they do not
measure whether sound was audible. Unavailable players end tracking. Plays
started outside Soundbox are not tracked. Reports are best effort: outages,
restarts or incomplete player state reporting can leave gaps.

These reports teach the selected adaptive mode and the active Morning or Night
profile. Direct and Ordered playback can teach the time profile but do not
create a Direct or Ordered learned preference. Explicit More/Less feedback
works with listening activity sharing disabled. Soundbox's account-wide
learning switch also prevents automatic learning from these reports.

## Assist voice phrases

Home Assistant deliberately keeps custom sentence grammars in its own
`config/custom_sentences` directory; a HACS integration cannot silently write
there. The Soundbox intent handlers are installed with the integration. To
enable the included phrases, copy either `sentences/en/soundbox.yaml` or
`sentences/de/soundbox.yaml` into the matching
`config/custom_sentences/<language>/soundbox.yaml` path and reload Assist (or
restart Home Assistant). This is the only manual voice-specific step.

The pack covers phrases such as “play Smart Radio in the kitchen”, “next
Soundbox station in the kitchen”, “play this more”, and “save this song”.

## Privacy

Normal operation sends Soundbox authentication requests, random session
identifiers, station and radio-mode selections, and any More/Less or Save Song
requests you make. The mapping from those sessions to speakers stays in Home
Assistant. Station source URLs are not returned to Home Assistant; players use
Soundbox's HTTPS playback URLs.

**Share listening activity is off by default.** When you enable it, the
integration additionally sends random playback identifiers, event sequence
numbers, station IDs, radio-mode IDs, playback events, and estimated listening
seconds, as described above. Disabling it stops further listening reports; it
does not erase learning already stored in your Soundbox account. You can reset
stored learning in Soundbox's website. Temporary playback deduplication records
expire after 30 days and are removed by Soundbox's daily cleanup; deleting the
Soundbox account also removes them.

Room names, speaker names, entity IDs and unrelated Home Assistant state are
never included in these requests. The integration does not record or upload
microphone audio. Save Song asks Soundbox to identify the station's stream.

## Removal

Remove the Soundbox entry from **Settings → Devices & services** to stop its
local sessions and listeners. Disconnect Home Assistant in Soundbox's website
settings to revoke the account connection. HACS can then uninstall the files.

## Development

Tests run against Home Assistant core using
[`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest tests -q --cov=custom_components.soundbox
```

The suite uses real Home Assistant setup, OAuth flows, media-source resolution,
actions and state-change handling, with mocked Soundbox responses and simulated
speaker states. Physical speakers have not been tested; compatibility depends
on the selected player's stream support and state/media-ID reporting.

The config flow is covered end to end, which is what the Home Assistant quality
scale asks for at Bronze; `custom_components/soundbox/quality_scale.yaml`
records where every Bronze rule stands.

## Notes

The integration lives at `custom_components/soundbox`. Run Home Assistant's
standard integration tests with a development checkout of Home Assistant.
