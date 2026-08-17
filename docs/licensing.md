# Data licensing — Trailforks and OpenStreetMap

Status as of 2026-08-17. **This is an engineering brief, not legal advice.** It
records what the terms say, what the code actually does, and where the two
meet. The questions marked FOR COUNSEL need a lawyer; the rest are ours.

## Bottom line

1. **No Trailforks data has ever entered this system.** The live ingestion path
   is an unimplemented stub, and the fixture is synthetic. There is nothing to
   remediate — only a decision to make before writing the live path.
2. **Trailforks' terms do not permit what VaiVia is** without prior written
   consent from Outside. Commercial use, use in a software program, and AI use
   are each named separately. This is not a close reading; it is the stated
   default.
3. **Our OSM attribution is inadequate today**, and that is unrelated to
   Trailforks. It is the one thing on this page that needs fixing regardless of
   which direction the product takes.

## 1. What the terms say

Sources read 2026-08-17. Trailforks serves 403 to automated fetchers; these were
read in a browser.

### Trailforks Data Use Policy (<https://www.trailforks.com/about/data/>)

The policy is four sentences, quoted in full:

> Use of Trailforks data is only allowed via the Trailforks API. You must be
> granted an access key to the API.
> You can only use the Trailforks data via the Trailforks API.
> You can not copy Trailforks data for non personal use.
> You must attribute any use of the data to Trailforks, or works produced with
> the data.

It also states it "is subject to change", and that it does not apply to the
embeddable widgets or RSS feeds, which *may* be placed on pages with
advertising.

### Trailforks API page (<https://www.trailforks.com/about/api/>)

> A JSON API is available upon approval to integrate Trailforks data into your
> application or website. This access is available upon request but not
> guaranteed. Please fully explain the benefits your project will bring to
> Trailforks and its community in your request.

> Access is not guaranteed or usually granted to students and individuals for
> personal projects and websites.

Authentication is `app_id` + `app_secret`, and "All requests to the API are
logged."

### Outside Terms of Use (<https://www.trailforks.com/about/tou/>)

Trailforks has been owned by Outside Interactive, Inc. since 2021; the ToU was
last updated 2025-10-03. Section 4.1:

> The Services are available for your personal, noncommercial use. Noncommercial
> use does not include the use of the Services without prior written consent
> from Outside in connection with the development of any software program,
> including, but not limited to, training a machine learning or artificial
> intelligence (AI) system.

Section 5.2 grants only a "limited, revocable, personal, non-transferable and
non-exclusive" licence for "personal, noncommercial purposes", conditioned on
not copying, modifying, creating derivative works, or commercially exploiting
the content.

Section 5.6 separately prohibits automated collection ("robots, spiders,
scripts... designed to data mine or scrape") and, as its own bullet:

> Use the Content for the development of any software program, including, but
> not limited to, training a machine learning or artificial intelligence (AI)
> system.

Licensing and legal contact: `legal@outsideinc.com`, Outside Interactive, Inc.,
5565 Arapahoe Ave, Unit F, Boulder, CO 80303, Attn: Legal Department.

### OpenStreetMap (ODbL)

OSM data is © OpenStreetMap contributors under the Open Database Licence.
Attribution is required for Produced Works; share-alike attaches to Derivative
Databases that are publicly used. Under the OSMF
[Collective Database Guideline](https://wiki.openstreetmap.org/wiki/Collective_Database_Guideline),
where non-OSM data forms an independent data type alongside OSM data, the
combination is treated as a Collective Database and share-alike applies only to
the OSM-derived parts.

## 2. What the code actually does

Audited 2026-08-17 against the working tree.

### Nothing is fetched from Trailforks

`backend/ingestion/trailforks_ingest.py:207-211` is the only live path and
raises `NotImplementedError`. There is no Trailforks HTTP client, no endpoint
path, no query parameters. `trailforks_api_key` and `trailforks_base_url` exist
in `backend/core/config.py:35-36` but are read by nothing. The cache directory
`backend/fixtures/trailforks_cache/` does not exist on disk; it is gitignored
pre-emptively.

`backend/fixtures/trailforks_mock.json` is synthetic: the prose, ids and
difficulty ratings are hand-authored, and the geometry is re-cut from ingested
OSM ways by `backend/scripts/make_trailforks_fixture.py`. It contains no
Trailforks content.

### What the live path *would* persist

If `fetch_live()` were implemented against the current schema, these `(:Trail)`
properties would hold Trailforks-sourced values (`trailforks_ingest.py:31-54`,
`:147-180`): `id`, `name`, `activity`, `difficulty`, `difficulty_notes`,
`description`, `landscape_description`, `elevation_gain_m`, `elevation_loss_m`,
`best_seasons`, and the four `hazards_<season>` lists. `trailforks_url` is
constructed locally from the record's `alias`. Geometry is used for matching and
then discarded, not stored on the Trail.

### Derived data — the AI exposure point

`backend/core/embeddings.py:46-75` builds the embedding input by concatenating
`description`, `landscape_description`, `difficulty_notes`, difficulty/activity,
and best seasons — all Trailforks-sourced — with OSM POI names appended. That
vector is stored on the Trail as `description_embedding`
(`backend/scripts/embed_trails.py:55-60`).

At query time, retrieved rows including `difficulty_notes` and
`landscape_description` are serialized into the prompt sent to OpenAI
(`backend/chat/llm.py:87-98`), and `backend/chat/prompts.py:74-77` instructs the
model to reproduce `difficulty_notes` and cite `trailforks_url`.

### What reaches end users

| Field | Reaches browser | Rendered |
|---|---|---|
| `difficulty_notes` | yes, every trail template | yes — `TrailCard.tsx:59`, and repeated in LLM prose |
| `landscape_description` | yes, in payload | no |
| `trailforks_url` | yes | yes — outbound link, `TrailCard.tsx:61-71` |
| `description` | via `GET /trails/{id}` only | no — frontend never requests it |

`gateway/src/app.ts:23` proxies the whole `/trails` prefix, so `description` is
reachable by any authenticated client even though the UI does not ask for it.

### Attribution present today

The only attribution string in the repo is `'© OpenStreetMap contributors'` on
the MapLibre raster tile source (`frontend/components/MapView.tsx:22`), rendered
in a `compact: true` (collapsed) control. There is no Trailforks attribution
anywhere, no attribution field on any API response, and no footer, About page or
credits component.

## 3. Gap analysis

### If live Trailforks ingestion were switched on tomorrow

Each of these would need to be true, and none is today:

- An approved API key and an executed agreement covering **commercial** use, use
  **in a software program**, and **AI/derived-data** use. All three are named in
  the ToU as requiring prior written consent.
- Visible Trailforks attribution wherever their data or works produced from it
  appear — currently absent everywhere.
- A defensible position on storing their prose in Neo4j and deriving embeddings
  from it. Section 5.2 forbids derivative works absent consent; an embedding of
  their description is a derived representation of it.
- A position on passing their text to OpenAI at inference time.
- Confirmation that no restricted content (e.g. hidden trail GPS tracks) is
  exposed, per the Data Use Policy.

### Present-tense, regardless of Trailforks: our OSM attribution is thin

The app ships OSM-derived segment geometry, POI names and routing results to end
users. The single tile-layer attribution credits the **tiles**, not the
**data**, and it is collapsed by default. ODbL requires attribution for Produced
Works. This is the clearest and cheapest thing to fix on this page, it is
entirely within our control, and it is required today — not contingent on any
Trailforks outcome.

The good news on the OSM side: because ingestion never merges OSM and Trailforks
nodes — they stay separate node types joined by `COMPOSED_OF` — the graph is
structured the way the Collective Database Guideline describes, which keeps
share-alike scoped to the OSM-derived parts. An architectural decision made for
data-quality reasons turns out to help here.

## 4. Options

**A. Apply for API access and written consent.** The only compliant route to
using their data. Their process explicitly asks applicants to justify community
benefit, and explicitly discourages individual/small projects. Disclose the
commercial and AI use up front — a permission granted on an incomplete
description is worth nothing. Draft request in the appendix. Assessment:
genuinely uncertain. Do not build a roadmap that assumes approval. Note that
Trailforks' largest distribution partner, Gaia GPS, is a sibling company inside
Outside rather than an arms-length licensee, which is weak evidence that
third-party commercial licensing is uncommon.

**B. Ship OSM-only.** Removes the blocker entirely and is fully compatible with
a commercial consumer product, subject to ODbL attribution and share-alike.
Costs the curated layer: named trail loops, editorial difficulty ratings, and
the prose that currently feeds the embedding. Before pricing this as a large
loss, note that OSM natively carries `mtb:scale`, `sac_scale`, `surface`,
`trail_visibility` and `route=hiking/mtb` relations — which covers a meaningful
share of what Trailforks supplies. Worth a spike to quantify.

**C. Own or community-sourced curation.** Longer road, but the curated layer
becomes an asset rather than a dependency, and it removes a supplier who is also
a potential competitor. Compatible with B.

Options B and C do not foreclose A; A alone does foreclose shipping on schedule
if the answer is slow or no.

## 5. Open questions FOR COUNSEL

1. Does generating retrieval embeddings from licensed text, and passing that
   text to a third-party LLM at inference, fall under ToU 5.6's prohibition on
   using Content "for the development of any software program, including...
   training a machine learning or AI system"? The clause names *training*;
   we do not train. Section 4.1 is broader and may catch it regardless.
2. Does storing their prose in our database and serving it to our users
   constitute a prohibited "derivative work" or "copy... for non personal use"
   under the Data Use Policy, even when accessed via the approved API?
3. Is deep-linking to `trailforks.com/trails/<alias>/` acceptable independent of
   any data licence? (We construct these links locally; they need no API.)
4. What attribution form and placement satisfies both "You must attribute any
   use of the data to Trailforks" and ODbL simultaneously?
5. EU angle: the product targets an Italian launch market while Outside's ToU
   specifies Colorado law and JAMS arbitration.

## 6. Recommended next actions

1. **Fix the OSM data attribution now.** Not blocked on anything or anyone.
2. **Re-triage the handoff blocker.** It is currently logged `severity: low`
   with the framing "fixture is synthetic". Given the terms, this is a
   strategy-level constraint on the product, not a data-plumbing task.
3. **Correct `docs/data-sources.md` and `docs/fragilities.md` §4**, which
   document a live API integration, rate-limit backoff, bbox chunking and a
   cache layer — none of which exist. A reader would reasonably conclude the
   live path is nearly ready.
4. **Decide between A and B before Phase 6 closes**, since deploy plumbing and
   GTM messaging both depend on which data story is true.
5. If A: send the appendix request, and expect to wait.

---

## Appendix — draft API access request

**Unsent draft. Review, edit, and send it yourself.** Do not send without
deciding whether you are comfortable disclosing the AI architecture in this
detail; the alternative is asking a narrower question first, but a permission
obtained on a partial description would not protect the product.

To: via <https://www.trailforks.com/contact/> (API access) — legal questions to
`legal@outsideinc.com`

Subject: API access request — VaiVia, conversational trail discovery (Lecco /
Bergamo, Italy)

> Hello,
>
> I'm writing to request Trailforks API access for VaiVia, a route-planning app
> for hikers and mountain bikers covering the Lecco and Bergamo areas of
> Lombardy, Italy.
>
> **What it does.** Users describe the outing they want in plain language — "an
> easy lakeside ride under 15 km that passes a hut" — and VaiVia finds matching
> trails. Behind it is a knowledge graph that combines OpenStreetMap path
> geometry with curated trail metadata, which is where Trailforks would come in.
>
> **What I would use.** Trail-level metadata for the two regions above: name,
> activity, difficulty rating and notes, distance, elevation, descriptions and
> seasonality. I would not ingest or expose hidden or restricted GPS tracks, and
> I would not redistribute your data in bulk or offer it for download.
>
> **How it would be used technically, stated plainly.** VaiVia is a commercial
> product and it uses AI, so I want to be explicit rather than have you discover
> it later. Trail descriptions would be stored in our database and converted
> into vector embeddings so that a user's free-text description can be matched
> against trails by meaning. At query time, the retrieved trail records are
> passed to a large language model that writes the reply. No model is trained or
> fine-tuned on your data at any point, and the model is never able to query
> your API or our database directly. If any part of that is unacceptable, I
> would rather know now.
>
> **What Trailforks gets.** Every trail VaiVia surfaces already carries a direct
> link back to its Trailforks page, in both the trail cards and the written
> answers — that is implemented today. Attribution to Trailforks would appear
> wherever the data or answers derived from it are shown. The app is a discovery
> layer that sends users to you for conditions, reports and photos; it does not
> reproduce your ride logs, reports or community features. The launch region is
> the Italian Alps, where I believe adding a natural-language discovery route
> into your data could bring in riders and hikers who would not otherwise search
> Trailforks directly.
>
> **Where it stands.** The product works end to end today against a synthetic
> fixture. I have deliberately not called your API, because your Data Use Policy
> requires an access key first.
>
> I am happy to discuss commercial terms, volume limits, caching rules or
> attribution requirements, and to sign whatever agreement you consider
> appropriate. If API access is not available for a project like this, I would
> appreciate knowing that directly so I can plan around it.
>
> Thank you for considering it.
>
> Oscar Arroyo Vega
> AI Safe Earth — <arroscar@gmail.com>
