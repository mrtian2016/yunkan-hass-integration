# Brand assets for home-assistant/brands

These icons are staged for submission to
[home-assistant/brands](https://github.com/home-assistant/brands), which is what
gives the integration its logo in the Home Assistant UI and HACS. Brand
submission is a separate pull request to that repository and is **not** required
for the integration to work through a HACS custom repository.

## How to submit

1. Fork `home-assistant/brands`.
2. Copy the icons into `custom_integrations/yunkan/`:
   - `icon.png` — 256×256, transparent background
   - `icon@2x.png` — 512×512, transparent background
3. Open a pull request. The brands CI checks dimensions and transparency.

The domain (`yunkan`) must match the integration's `manifest.json` `domain`.

Source artwork: `icon@2x.png` here is the master 512×512; `icon.png` is the
256×256 downscale.
