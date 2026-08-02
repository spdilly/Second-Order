# Image Credits and Treatment

All photographs sourced from Unsplash under the Unsplash License (free for
commercial use, no attribution required). Credits kept for provenance.
Harvested 2026-07-03 from Unsplash search results; source URLs are the
images.unsplash.com CDN bases as served.

## Treatment recipe (applied to every image)

1. Convert to grayscale.
2. Autocontrast with 1% clip.
3. Three-point duotone LUT:
   - shadows -> #16181B (charcoal)
   - midtones -> #4C6E6B (subtle teal-gray)
   - highlights -> #F0F7F6 (near-white, slightly teal)
4. Center-crop to landscape (2.4:1 bands and case headers, 3:1 strips,
   crop biased 42% from top).
5. Save as progressive optimized JPEG, quality stepped down until under
   250KB.

Script: scratchpad treat_images.py + recrop.py (session 2026-07-03).

## Files

| File | Placement | Source (CDN base) | Photographer | Size |
|---|---|---|---|---|
| band-concrete-wave.jpg | index.html band between Four Doors and featured case | https://images.unsplash.com/photo-1486718448742-163732cd1544 | unsplash.com/@rgaleriacom | 242KB |
| strip-facade-shadow.jpg | work.html strip under page header | https://images.unsplash.com/photo-1774203078966-69151033ded8 | unsplash.com/@visualsbyamor | 156KB |
| case-wall-shadows.jpg | work.html childcare case header | https://images.unsplash.com/photo-1759596351257-f00dc312e0d0 | unsplash.com/@cherstve_pechivo | 40KB |
| case-spiral-stair.jpg | work.html SFH tool case header | https://images.unsplash.com/photo-1619162668585-a916cd9fac8a | unsplash.com/@zgc1993 | 76KB |
| band-curved-structure.jpg | spare | https://images.unsplash.com/photo-1565768502473-c5dc73b7eb33 | unsplash.com/@jonnyjames2 | 201KB |
| band-curved-building.jpg | spare | https://images.unsplash.com/photo-1555940556-38836cc3ef7c | unsplash.com/@zhpix | 206KB |
| strip-facade-white.jpg | spare | https://images.unsplash.com/photo-1518277140448-c3822bc657e4 | unsplash.com/@bernardhermant | 62KB |
| case-dark-abstract.jpg | spare | https://images.unsplash.com/photo-1762008310452-3f53aa66c276 | unsplash.com/@mak_jp | 38KB |
| case-concrete-wall.jpg | spare | https://images.unsplash.com/photo-1635074155443-6cbf74711dd2 | unsplash.com/@bmkristiansen | 95KB |
| case-concrete-stairs.jpg | spare | https://images.unsplash.com/photo-1620902740358-c07fe4916812 | unsplash.com/@rgaleriacom | 191KB |
