# Module 1 — City Journal

A personal place journal for a city I just moved to. **Memory, not discovery**: it
remembers places *I* have been or want to go, and lets me find them again when I
half-remember them ("where was that coffee shop with the good wifi?").

Explicitly **not** a recommender, not a "what should I do today" tool, not a
neighborhood guide, not an errand/logistics helper.

## Stack

- Python + **Django** (module requirement), SQLite via the Django ORM.
- Primary interface: **Django management commands** (`python manage.py <cmd>`).
- Secondary interface: **Django admin** for browsing/editing the same data in a
  browser (`runserver` + localhost:8000). ~5 lines in `admin.py`.
- **Fully offline.** No API keys, no network calls, no geocoding. Every field is
  typed by me.

## Data model

`Place`
- `name` — required
- `neighborhood` — free text
- `address` — optional, free text (not geocoded)
- `tags` — many-to-many with `Tag` (coffee, ramen, park, wifi, cheap, ...)
- `note` — free text, the thing fuzzy search actually leans on
- `rating` — 1–5, nullable (a wishlist place has no rating yet)
- `status` — `wishlist` | `visited`
- `created_at`, `last_visited_at`

`Tag`
- `name` — unique, lowercased on save

## Commands

| Command | Behavior |
|---|---|
| `add` | Create a place. Flags for tags/note/rating/neighborhood/status. Defaults to `wishlist` if no rating given. |
| `find <query>` | **Fuzzy + ranked keyword** search over name, note, tags, neighborhood. Typo-tolerant. Optional `--tag`, `--status`, `--neighborhood` filters. Never returns nothing: below the score threshold it falls back to the closest 3, labelled as weak matches. |
| `todo` | List `wishlist` places — what I haven't gotten to yet. Filterable by tag/neighborhood. |
| `visit <place>` | Flip a place to `visited`, stamp `last_visited_at`, prompt for note + rating. |
| `surprise` | Pick one place to go to — random, biased toward long-unvisited or never-visited. Fights the "same three spots" rut. |
| `stats` | Coverage: places per neighborhood, top tags, visited vs wishlist counts, how many neighborhoods touched. |

## Retrieval

Fuzzy + ranked keyword, fully offline (`rapidfuzz` or equivalent). Score across
name/note/tags/neighborhood, rank, show top N with the matched field highlighted.
No embeddings, no LLM.

**No-result fallback.** A fuzzy search that returns silence is worse than one that
guesses. If nothing clears the confidence threshold, `find` still prints the three
highest-scoring places, under a header that says they are weak matches (e.g.
`No strong match. Closest 3:`). The user decides; the tool never shrugs.

## Features (the four we settled on)

1. **Capture a place in one line.** `add` records a place with neighborhood,
   free-text note, tags, and an optional rating. Low enough friction that I log a
   spot while still standing outside it.

2. **Find it again when I only half-remember it.** `find` does typo-tolerant,
   ranked search across name, note, tags, and neighborhood, and never comes back
   empty: below the confidence threshold it offers the closest 3 as weak matches.

3. **Want-to-go → been-there loop.** Every place is `wishlist` or `visited`.
   `todo` shows what I still owe the city; `visit` flips a place over and asks
   for the note and rating while the memory is fresh.

4. **Nudges and coverage.** `surprise` picks somewhere to go, favoring the
   never-visited and the long-neglected. `stats` shows how much of the city I've
   actually touched — places per neighborhood, top tags, visited vs wishlist.

## Out of scope for v1

- Geocoding, distance/"near me" queries, maps
- Places-API enrichment (hours, categories, external ratings)
- Semantic search / embeddings / LLM-generated answers
- Export to markdown or CSV
- Custom web views beyond the stock admin
- Photos, multi-user, auth beyond the admin superuser

## Done means

`add`, `find`, `todo`, `visit`, `surprise`, `stats` all work against SQLite;
admin lists and edits the same records; tests cover the fuzzy ranking and the
wishlist→visited transition; README shows a real session with real places from
my city.
