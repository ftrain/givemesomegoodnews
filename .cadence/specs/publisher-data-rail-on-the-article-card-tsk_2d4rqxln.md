# Publisher data rail on the article card

Replace the current `<aside class="tagcol">` in `render_feed_item()` (build_site.py ~line 826) with a publisher rail that answers "who is this outlet?" at a glance: state flag and abbreviation, a locator map with a region name, ownership/mission status tags in a capped priority order, publishing cadence, and a support link. Today the aside carries only a subject lozenge, tag links, and a support lozenge that falls back to the newsroom homepage — none of the state identity, map, cadence, or fallback behaviour the feature requires exists yet.

Everything renders server-side into the committed static site; there is no JS framework and no test suite, so each child verifies by building (`make build`) and inspecting generated HTML under `site/`.

## In scope

- The rail's contents and per-element fallbacks, delivered by the four child tasks
- New helper functions in `build_site.py` plus supporting modules (flag assets, locator map, cadence query)
- Rail markup and rail-specific CSS rules inside `stylesheet()`

## Out of scope

- Where the rail sits relative to the story column across breakpoints — that is the Mobile-First Card Layout task
- The story-side elements of the card (date, headline, byline, photo, body) — Story Main Column
- The expandable publication profile — Inline Profile Disclosures

## Acceptance criteria

- A card for a newsroom with a state shows flag, abbreviation, locator map, region name, status tags, cadence, and support link
- Every rail element has the specified fallback: national marker, coarse-area map, collapsed tag area, withheld cadence, collapsed support area
- No rail element is left as a labelled empty space when its data is missing
- `make build` completes and the generated feed pages render the rail on every listing
