# AMS Logos

White logos on transparent background for multi-material system identification.

## Regenerating PNGs from SVGs

See [BUILD_SYSTEM.md - SVG to PNG Conversion](../../../docs/BUILD_SYSTEM.md#svg-to-png-conversion) for details on using `rsvg-convert`.

```bash
# Regenerate all logos at 64x64:
for svg in *.svg; do
  rsvg-convert "$svg" -w 64 -h 64 -o "${svg%.svg}_64.png"
done
```

## Supported Systems

| File | System |
|------|--------|
| box_turtle | AFC / Box Turtle |
| ercf | ERCF (Enraged Rabbit) |
| 3ms | 3MS |
| tradrack | Tradrack |
| mmx | MMX |
| night_owl | Night Owl |
| quattro_box | Quattro Box |
| btt_vivid | BTT ViViD |
| kms | KMS |
