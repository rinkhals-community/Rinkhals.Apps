# Thumbnail Gradient Background

## File
`thumbnail-gradient-bg.png` (300x300)

## Purpose
Provides a subtle diagonal gradient background for print file thumbnail cards in the print selection panel.

## Gradient Specifications
- **Start color (lower-left):** rgb(80, 80, 80) - Medium gray
- **End color (upper-right):** rgb(0, 0, 0) - Black
- **Direction:** Diagonal from lower-left corner to upper-right corner
- **Size:** 300x300 pixels
- **Bit Depth:** 24-bit (8 bits per channel)

## How to Regenerate

Using ImageMagick's sparse-color feature with Barycentric interpolation:

```bash
magick -size 300x300 -depth 24 xc: \
  -sparse-color Barycentric '0,299 rgb(80,80,80) 299,0 rgb(0,0,0)' \
  thumbnail-gradient-bg.png
```

### Command Explanation
- `-size 300x300` - Create a 300x300 canvas
- `-depth 24` - Use 24-bit color depth (8 bits per RGB channel)
- `xc:` - Blank canvas
- `-sparse-color Barycentric` - Use Barycentric interpolation for smooth gradients
- `'0,299 rgb(80,80,80)'` - Lower-left corner (x=0, y=299) is medium gray
- `'299,0 rgb(0,0,0)'` - Upper-right corner (x=299, y=0) is black

The Barycentric method creates a smooth diagonal gradient between the two specified points.

## Design Notes

### Gradient Quality
- Clean gradient generated at 24-bit color depth
- Suitable for SDL simulator display (32-bit ARGB8888)
- Gray value (80) is approximately 2x brighter than the original subtle gray (42)
- Diagonal direction provides visual interest and depth to thumbnail cards

### Dithering Considerations

**SDL Simulator (current):**
- No dithering needed - SDL renders at 32-bit ARGB8888
- Gradient displays smoothly without banding artifacts

**Production Hardware (framebuffer target):**
- Hardware displays at 16-bit RGB565 (5-6-5 bits per channel)
- May exhibit color banding when converting from 24-bit PNG to RGB565
- **Future optimization:** Implement Floyd-Steinberg dithering in LVGL's rendering pipeline

### Future Dithering Implementation

For production builds targeting 16-bit RGB565 displays, consider implementing real-time dithering using the technique described in:

**Reference:** https://forum.lvgl.io/t/real-time-dithering-from-24-to-16-bit-for-beautiful-displays/18642

**Approach:**
1. Set `LV_COLOR_DEPTH 24` in lv_conf.h
2. Implement custom display flush callback with Floyd-Steinberg dithering
3. Convert RGB888 → RGB565 with error diffusion during framebuffer writes

**Note:** This optimization is not needed for SDL simulator builds, only for embedded hardware with native RGB565 displays.
