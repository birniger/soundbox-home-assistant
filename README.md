# Soundbox for Home Assistant

Soundbox brings a personal, adaptive internet-radio library to Home Assistant.
It is a media source and a set of actions for your existing speakers; it never
pretends to be a speaker itself.

## What it does

- Browse a flat list of playable saved stations across all connected accounts
  from **Media** in the Home Assistant sidebar, and play any of them on any
  media player. Soundbox appears as a media source, so the speaker fetches the
  stream itself.
- Play a named station or Smart, Morning, Night, Local, Favorites, Ordered, or
  Explore Radio on any Home Assistant media player through the `soundbox.play`
  action or Assist.
- Keep separate Soundbox sessions for separate rooms, so Next, More/Less, and
  Save Song act on the right speaker.
- Use Home Assistant Assist actions, including play, next, previous, more/less,
  and save-song. A ready-made English and German sentence pack is included.

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

Soundbox receives a random session identifier and station choices. It does not
receive Home Assistant room names, speaker names, or home state. Station source
URLs are never returned to Home Assistant.

## Development

Tests run against a real Home Assistant using
[`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest tests -q --cov=custom_components.soundbox
```

The config flow is covered end to end, which is what the Home Assistant quality
scale asks for at Bronze; `custom_components/soundbox/quality_scale.yaml`
records where every Bronze rule stands.

## Notes

The integration lives at `custom_components/soundbox`. Run Home Assistant's
standard integration tests with a development checkout of Home Assistant.
