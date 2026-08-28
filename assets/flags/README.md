# Flags

One SVG per state, the district, and the five territories, named by the
lowercased two-letter code the catalog stores in `orgs.state`. `build_site`
copies the directory to `site/flags/` and the publisher rail points an
`<img>` at it.

Sourced 2026-08-28 from Wikimedia Commons (`Special:FilePath/Flag of <name>.svg`)
and passed through `svgo`. The flags of US states and territories are their
governments' own insignia and are in the public domain; Commons files
carrying them are tagged accordingly.

They are committed rather than fetched: the build runs offline against
Postgres, and `site/` is served as it is committed.
