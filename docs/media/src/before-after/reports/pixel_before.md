<!-- slop-gate-report -->
## slop-gate: ❌ Blocked

16 blocking, 0 advisory. Checked 5 media files at `HEAD`.

Errors block the merge; warnings are advice. Fix the errors, push, and this report updates. To accept a finding on purpose, see [suppressing findings](https://github.com/mikejmckinney/slop-gate#suppressing-a-finding).

### media (16 blocking, 0 advisory)

_Image and audio checks against the media manifest in .slop-gate.yml._

| | Location | Rule | Problem and fix |
|---|---|---|---|
| 🛑 | `assets/sprites/knight_gemini-3-pro-image.png` | `size` | Image is 1024×1024; the manifest declares 32x32. **Fix:** Resize or crop to the declared size, or update the manifest if the design changed. |
| 🛑 | `assets/sprites/knight_gemini-3-pro-image.png` | `alpha-missing` | The manifest requires transparency, but every pixel is opaque. **Fix:** Export with an alpha channel and a transparent background. |
| 🛑 | `assets/sprites/knight_gemini-3-pro-image.png` | `palette` | 65818 distinct colors; the manifest allows 16. Extra colors usually come from anti-aliasing, gradients, or a resampled upscale. **Fix:** Quantize to the project palette with no dithering, then hand-fix clusters. |
| 🛑 | `assets/sprites/knight_gemini-3.1-flash-image.png` | `size` | Image is 1024×1024; the manifest declares 32x32. **Fix:** Resize or crop to the declared size, or update the manifest if the design changed. |
| 🛑 | `assets/sprites/knight_gemini-3.1-flash-image.png` | `alpha-missing` | The manifest requires transparency, but every pixel is opaque. **Fix:** Export with an alpha channel and a transparent background. |
| 🛑 | `assets/sprites/knight_gemini-3.1-flash-image.png` | `palette` | 65759 distinct colors; the manifest allows 16. Extra colors usually come from anti-aliasing, gradients, or a resampled upscale. **Fix:** Quantize to the project palette with no dithering, then hand-fix clusters. |
| 🛑 | `assets/sprites/knight_gpt-image-2.5-sunburst.png` | `size` | Image is 1024×1024; the manifest declares 32x32. **Fix:** Resize or crop to the declared size, or update the manifest if the design changed. |
| 🛑 | `assets/sprites/knight_gpt-image-2.5-sunburst.png` | `soft-alpha` | 328245 semi-transparent pixels. Pixel art needs hard edges (alpha 0 or 255). **Fix:** Threshold the alpha channel and clean edges at native size. |
| 🛑 | `assets/sprites/knight_gpt-image-2.5-sunburst.png` | `palette` | 52461 distinct colors; the manifest allows 16. Extra colors usually come from anti-aliasing, gradients, or a resampled upscale. **Fix:** Quantize to the project palette with no dithering, then hand-fix clusters. |
| 🛑 | `assets/sprites/knight_mai-image-2.5-pro.png` | `size` | Image is 1024×1024; the manifest declares 32x32. **Fix:** Resize or crop to the declared size, or update the manifest if the design changed. |
| 🛑 | `assets/sprites/knight_mai-image-2.5-pro.png` | `alpha-missing` | The manifest requires transparency, but every pixel is opaque. **Fix:** Export with an alpha channel and a transparent background. |
| 🛑 | `assets/sprites/knight_mai-image-2.5-pro.png` | `palette` | 77346 distinct colors; the manifest allows 16. Extra colors usually come from anti-aliasing, gradients, or a resampled upscale. **Fix:** Quantize to the project palette with no dithering, then hand-fix clusters. |
| 🛑 | `assets/sprites/knight_seedream-5-0-pro.png` | `format-mismatch` | File is JPEG data with a `.png` extension. **Fix:** Re-encode to the extension's format or rename the file. |
| 🛑 | `assets/sprites/knight_seedream-5-0-pro.png` | `size` | Image is 2048×2048; the manifest declares 32x32. **Fix:** Resize or crop to the declared size, or update the manifest if the design changed. |
| 🛑 | `assets/sprites/knight_seedream-5-0-pro.png` | `alpha-missing` | The manifest requires transparency, but every pixel is opaque. **Fix:** Export with an alpha channel and a transparent background. |
| 🛑 | `assets/sprites/knight_seedream-5-0-pro.png` | `palette` | 96519 distinct colors; the manifest allows 16. Extra colors usually come from anti-aliasing, gradients, or a resampled upscale. **Fix:** Quantize to the project palette with no dithering, then hand-fix clusters. |
