# The social layer — photos, comments, reactions

Designed 2026-08-20. **Nothing is built.** This is the shape the store takes when the
feature is wanted, written now because one of its requirements lands on route generation
*before* the feature exists, and finding that out afterwards would be expensive.

## The requirement it imposes today

**A route id must be stable across rebuilds.** A comment keys to `route.id`. If the
pipeline regenerates its catalogue and route ids change, every comment, photo and like
orphans silently — the rows still exist, they just point at nothing, and nobody notices
until a user asks where their photo went.

This is not a Mongo concern. It is a constraint on `pipeline/draw/`, which has not been
written yet, and it is the reason this document exists now rather than later:

- `osm-relation-<id>` is already stable — it is OSM's identity, not ours.
- A **generated** route must derive its id from something that survives a rebuild: its
  anchor vertex plus its shape, hashed, rather than a sequence number or a `run_id`.
  Vertex ids do **not** survive a rebuild (`build_network` truncates and reassigns), so
  the id must come from geometry — a rounded start coordinate and a geometry hash.
- When a regenerated route genuinely differs, that is a **new** route with a new id, and
  the old one is superseded rather than mutated. Comments stay attached to what they were
  written about.

Tracked as a next step against route generation, not against this document.

## What goes where

```
  route document (JSON)          MongoDB                    object storage
  the product, immutable         mutable, per-user          photo binaries
  rebuilt by the pipeline        written by users
        │                             │                           │
        └──────────── the API composes at READ time ───────────────┘
```

**User content is never merged into the route document.** The document is a build artefact
that must come out byte-identical from two runs of the same input — that property is how a
diff means something. Mixing in a like count would destroy it, and would also mean
rebuilding the catalogue every time somebody taps a heart. They compose in the API
response, not in the store.

## Collections

Mongo's fit here is the part that is genuinely document-shaped: a photo has EXIF a comment
does not, a comment has a thread a reaction does not, and the shapes will change as the
feature is used. One collection per kind, not one polymorphic `content` collection —
different validation, different indexes, different moderation rules.

### `photo`

```json
{
  "_id": "ObjectId",
  "route_id": "osm-relation-74613",
  "user_id": "supabase-uuid",
  "storage_key": "photos/2026/08/ab12…jpg",
  "variants": { "thumb": "…", "full": "…" },
  "width": 4032, "height": 3024, "bytes": 2841233,
  "caption": "Buco di Grigna in the mist",
  "taken_at": "2026-08-14T09:12:00Z",
  "point": { "type": "Point", "coordinates": [9.3812, 45.9401] },
  "distance_along_m": 6112.4,
  "status": "published",
  "created_at": "…", "updated_at": "…"
}
```

- **Binaries do not go in Mongo.** Object storage holds the file; this holds the key and
  the derived variants. GridFS would put multi-megabyte blobs in the same store as the
  queries, and photo bytes never need a query.
- `point` is optional and **opt-in**. See privacy below.
- `distance_along_m` is computed once against the route's geometry, so a photo can be
  placed on the elevation profile without recomputing per request.

### `comment`

```json
{
  "_id": "ObjectId",
  "route_id": "osm-relation-74613",
  "user_id": "supabase-uuid",
  "parent_id": null,
  "body": "The last 200 m are loose scree after rain.",
  "status": "published",
  "edited": false,
  "created_at": "…", "updated_at": "…"
}
```

`parent_id` gives one level of reply. Deeper nesting is a product decision nobody has made;
a flat thread with one reply level covers what a trail comment is for.

### `reaction`

```json
{
  "_id": "ObjectId",
  "route_id": "osm-relation-74613",
  "user_id": "supabase-uuid",
  "kind": "like",
  "created_at": "…"
}
```

**A reaction is a document, not a counter.** A counter cannot be un-liked without a race,
cannot answer "did I like this", and cannot be audited when a number looks wrong. The
displayed total is a `countDocuments` behind a short cache, or a maintained counter that
can always be rebuilt from these rows — never a number that is the only record.

Unique index on `(route_id, user_id, kind)`: liking twice is one like.

### Indexes

| collection | index | for |
|---|---|---|
| all three | `{route_id: 1, created_at: -1}` | the route page, newest first |
| all three | `{user_id: 1, created_at: -1}` | a user's own contributions, and deletion on request |
| `reaction` | `{route_id: 1, user_id: 1, kind: 1}` unique | one like per person |
| `photo`, `comment` | `{status: 1, created_at: -1}` | the moderation queue |

## Identity and access

No new auth. Identity is the Supabase user, and the existing rule holds without change:

- The **gateway stays the only public service**. It verifies the Supabase JWT and passes a
  verified `x-user-id` to the backend; the caller's bearer token is never forwarded.
- The **backend is the only thing that talks to Mongo.** Mongo is internal, exactly as
  Neo4j is. No browser-facing connection string, no client-side SDK.
- Write authorisation is a check in the backend against the verified `x-user-id`. Mongo has
  no equivalent of the Postgres RLS the Supabase tables rely on, which means the ownership
  check is application code and therefore **needs a test per rule** — the same discipline
  as `tests/test_query_loader.py` guarding the Cypher templates. This is the one place this
  design is weaker than the Postgres alternative, and naming it is the point.

## Moderation, privacy, abuse

None of this is optional once strangers can post:

- **`status` on every document** (`published` / `hidden` / `removed`), never a hard delete
  for moderation. A removed comment must stay auditable.
- **Reporting** is its own collection keyed to the reported document, with the reporter's
  id — one report per user per target.
- **Strip EXIF on upload**, and treat photo GPS as opt-in. A photo taken at home before
  setting out carries the user's home coordinates; publishing that because it was in the
  file is a real harm and a trivially avoidable one.
- **A user's contributions must be deletable in one operation** — the `{user_id}` indexes
  above exist for that as much as for the profile page.
- Rate limits belong at the gateway, where they already are, keyed on the verified user.

## The trade-off, stated once

Mongo is a **third** datastore: Neo4j for the graph, Supabase Postgres for chat history,
usage ledger and quotas, now Mongo for user content. Supabase could hold all of this today
in tables with RLS policies that put the ownership check in the database rather than in
application code, and it is already running, backed up and authenticated.

What earns Mongo its place is the expectation that these shapes move — that a photo grows
fields, that comments grow structure, that the next idea is not one of these three. If that
turns out not to be true, this is three collections that would have been three tables.

Worth revisiting when the feature is actually specified, not before.

## Postscript: personal favorites landed in Supabase (2026-08-21)

The first feature adjacent to this design arrived, and it took the Postgres road this
document left open. **Saved routes** (`route_favorites`, migration `0003`) are account
data, not social content: nobody else ever reads them, the shape is a bare
`(user_id, route_id, created_at)`, and the ownership check belongs in the database. The
owner chose Supabase for exactly the reasons the trade-off paragraph above names.

This does not pre-empt the reaction collection: a public like — counted, displayed,
composed with photos and comments — remains this document's design, still waiting to be
specified. What favorites did inherit from here is the id discipline: they key on
`route.id` alone, which is why a favorite survives the catalogue being replaced
wholesale per export, and why the API reports a vanished route as `missing` instead of
silently dropping the row.
