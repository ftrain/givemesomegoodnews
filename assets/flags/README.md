# Flags

One flag per state, the district, and the five territories, named by the
lowercased two-letter code the catalog stores in `orgs.state`. `build_site`
copies the `.webp` files to `site/flags/` and the publisher rail points an
`<img>` at them.

Sourced 2026-08-28 from Wikimedia Commons (`Special:FilePath/Flag of <name>.svg`)
and passed through `svgo`. The flags of US states and territories are their
governments' own insignia and are in the public domain; Commons files
carrying them are tagged accordingly.

They are committed rather than fetched: the build runs offline against
Postgres, and `site/` is served as it is committed.

## Why both an SVG and a WebP

The `.svg` files are the source art and are never served. Authentic
seal-on-blue flags are extremely detailed vector work — 79KB median, 496KB
for Idaho, 6MB for the set — and the rail draws them 24 pixels wide, where
none of that detail survives. Shipping them meant a reader paid megabytes
for smudges, and a card's flag could still be an empty box while the file
was in flight.

So each one is rendered once to a 96px-wide WebP (four times the rail's
24px, so a dense screen still has pixels to spend) and it is the WebP that
ships: 75KB for all 56, one to three kilobytes each, cheap enough that the
`<img>` loads eagerly and the flag is simply there. This is the same trade
`images.py` makes for feed photos, which are downscaled to 480px WebP
rather than served at source resolution.

The renders were made by loading the SVGs in headless Chromium at 96px and
compositing each one over white and over black, which recovers the alpha
channel exactly — Ohio's flag is a swallowtail burgee, not a rectangle, and
would otherwise pick up white corners on a dark background. To redo one
after a state redraws its flag, replace the `.svg` and render it the same
way.
