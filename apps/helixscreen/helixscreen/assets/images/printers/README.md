# Printer Images

This directory contains shipped printer images used in the first-run configuration wizard and the Printer Image picker overlay. Users can also add their own custom images (see [Custom Images](#custom-images) below).

## Available Images (13 printers)

### Voron Family
- ✅ `voron-0-2-4-750x930.jpg` - Voron V0.2 (120mm³ build)
- ✅ `voron-24r2-pro-5-750x930.webp` - Voron V2.4 (CoreXY, QGL)
- ✅ `voron-trident-pro-1-750x930.webp` - Voron Trident (3Z steppers)

### Creality K-Series
- ✅ `creality-k1-2-750x930.jpg` - Creality K1 (CoreXY, multi-MCU)

### FlashForge
- ✅ `flashforge-adventurer-5m-1-750x930.webp` - FlashForge Adventurer 5M
- ✅ `flashforge-adventurer-5m-pro-2-750x930.jpg` - FlashForge Adventurer 5M Pro

### Any cubic
- ✅ `anycubic-kobra.png` - Anycubic Kobra (LeviQ ABL)
- ✅ `anycubic-vyper.png` - Anycubic Vyper (Volcano hotend)
- ✅ `anycubic-chiron.png` - Anycubic Chiron (400×400mm)

### Rat Rig
- ✅ `ratrig-vcore3.png` - Rat Rig V-Core 3 (CoreXY); also used for V-Core Pro
- ✅ `ratrig-vcore4.png` - Rat Rig V-Core 4 (CoreXY); also used for V-Core 4 IDEX
- ✅ `ratrig-vminion.png` - Rat Rig V-Minion (Compact CoreXY)

### FLSUN
- ✅ `flsun-delta.png` - FLSUN Delta (QQ-S/Super Racer/V400)

### Elegoo
- ✅ `elegoo-centauri-carbon.png` - Elegoo Centauri Carbon

## Missing Images (Use Generic Fallback)

The following printers will use the generic Voron V2 image (`voron-24r2-pro-5-750x930.webp`) as fallback:

### Voron
- ❌ Voron V1/Legacy
- ❌ Voron Switchwire

### Creality Ender/CR Series
- ❌ Creality Ender 3
- ❌ Creality Ender 5
- ❌ Creality CR-10
- ❌ Creality CR-6 SE

### Prusa
- ❌ Prusa i3 MK3/MK3S
- ❌ Prusa MK4
- ❌ Prusa Mini/Mini+
- ❌ Prusa XL

## Image Specifications

- **Dimensions:** 750×930 pixels (standardized)
- **Format:** PNG, JPG, or WebP
- **Background:** White or transparent preferred
- **Content:** Full printer view, centered and cropped

## Custom Images

Users can add their own printer images without modifying this directory:

1. Place a PNG or JPEG file into `config/custom_images/` in the HelixScreen installation directory
2. Open the Printer Image picker (Home Panel → tap printer image → Printer Manager → tap image again)
3. Custom images appear automatically in the picker under the "Custom" section

**Requirements:** PNG or JPEG, maximum 5MB file size, maximum 2048x2048 pixels. HelixScreen auto-converts custom images to optimized LVGL binary format (300px and 150px variants) the first time the Printer Image picker overlay is opened.

Custom image selection is stored in the config as `"display.printer_image": "custom:filename"` (without extension).

## Adding New Shipped Images

1. Source high-quality printer image (product photos work best)
2. Resize to 750x930px with aspect ratio preservation:
   ```bash
   magick input.jpg -resize 750x930 -gravity center -extent 750x930 -background white output.png
   ```
3. Save to this directory with descriptive filename
4. Update this README
5. Update the Printer Image picker integration

## Future Work

- Add remaining Voron variant images (V1, Switchwire)
- Add Creality Ender/CR series images
- Add Prusa family images
- Consider adding manufacturer logos for unidentified printers
- Add more community-contributed shipped images
