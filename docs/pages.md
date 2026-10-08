# Pages and features

This document is the agreed scope of the site. It describes what each page
shows, where the data comes from, and what is deliberately left out. The
visual design follows it; the data model and ingestion jobs are derived from
it.

## Global rules

- **Navigation:** Home, Ask, Archive, About. On narrow screens the same four
  items form a bottom tab bar.
- **Interface language:** English or Japanese. English pages live at the root
  (`/archive`), Japanese ones under `/ja` (`/ja/archive`), so every language
  version has its own URL; each page links its counterpart and declares
  `hreflang` alternates. The switch in the header changes the path.
  `/sitemap.xml` lists every page in both languages; `/robots.txt` points at it.
- **Document language is independent of interface language.** Federal
  Reserve material is English; Bank of Japan material is Japanese. Every list
  item carries an original-language badge, and a document is always shown in
  its original language. Titles of the banks' regular documents (press
  conferences, statements, minutes, summaries of opinions, outlook reports)
  are rendered in the other language from patterns, using the publishers'
  own English names. Where a generated translation of a title or summary
  exists it is shown next to the original and labelled as generated.
- **Generated content is labelled.** Summaries, briefs, meeting titles and
  translations produced by a language model carry a visible label. Nothing
  generated is presented as the publisher's own text.
- **No visitor-dependent content at launch.** Rankings and ordering are
  computed from the published material itself, so every page has content on
  the first day. Signals derived from usage are blended in later without
  changing the user-facing definition.

## Home

| Area | Content | Source |
| --- | --- | --- |
| Hero | Ask box and a handful of curated sample questions | `domain/questions.yaml` |
| Main column: core archive | Ten most prominent recent releases across all publishers: publisher, kind, date, title, one-line summary, language badge | documents ordered by prominence (see below) |
| Side column: featured videos | Curated press conferences worth watching, each with a one-line reason | `domain/featured_videos.yaml` |

**Prominence** is a deterministic score over document attributes: kind weight
(statements and press conferences highest, minutes next, speeches and releases
after), recency decay, presence of video, seniority of the speaker, and pickup
by a third-party publisher such as the BIS speech library. Once questions have
been asked on the site, citation counts join the score as one more signal.

## Ask

- **Empty state:** ask box, sample questions, one sentence on what the archive
  covers.
- **A turn:** the question; three progress steps (searching, reading sources,
  writing); the answer as short paragraphs with numbered citation chips; a
  sources rail listing each cited passage with publisher, date, title and
  snippet. Citations into video transcripts carry a `mm:ss` badge. Hovering a
  chip highlights its source and vice versa.
- **Source drawer:** the cited passage in context, a link to the original
  document or video position, and a shortcut to open it in the archive.
- **Follow-ups:** three suggested follow-up questions; a sticky composer for
  the next question; previous turns stay on the page, collapsed.
- **Refusal state:** when a question falls outside the archive, the answer
  explains the scope in one paragraph and offers sample questions. No sources
  are shown.
- **Footnote on every answer:** answers use retrieved passages only, may
  contain errors, and should be checked against the original.
- Answers are written in the interface language; sources stay in their
  original language.

## Archive

A hub page with five sections, each also reachable directly.

| Section | Content | Source |
| --- | --- | --- |
| Latest | Newest twenty releases across all publishers; filters by publisher and kind; no date-range controls | documents |
| Briefs | Daily brief for each day with at least one release, and a meeting brief after each FOMC meeting and Monetary Policy Meeting; each brief cites the documents it draws on | generated, labelled |
| Videos | Every press conference, filterable by publisher; each card shows date, speaker, duration and whether the transcript has timestamps | media, documents |
| Rankings | Speakers, publishers and topics (below) | computed from documents and segments |
| Meetings | Timeline of policy meetings of both central banks; each meeting bundles its statement, press conference, minutes and projections or outlook report, with a generated title and summary and the extracted policy decision (rate, change in basis points, vote); a document not in the bundle is marked scheduled, not indexed, or not published for that meeting | meetings derived from documents by date |

**Rankings** are three boards computed from published material:

- **Speakers:** speeches given, press conferences held, minutes spoken, and
  pickups by the BIS speech library; selectable period (30 days, 90 days,
  one year, all time).
- **Publishers:** releases in the last 30 days broken down by kind.
- **Topics:** terms whose frequency in recent documents rose most against the
  previous period.

A community board based on site usage is not shown until real usage crosses a
threshold; before that the page simply has three boards.

## Document detail

Two forms share one layout: metadata, the document, and a side rail with
"ask about this document", links to the meeting it belongs to, and the link
to the original.

- **Press conference:** the publisher's own player embedded at the top;
  below it the transcript as speaker turns. Where the source provides
  captions the turns carry timestamps and clicking one seeks the video;
  otherwise the transcript is shown as question-and-answer blocks without
  timestamps.
- **Text document:** generated summary (labelled), then the full text in
  segments, each with an anchor so citations can point into it.

## About

What the site is, how a question is answered in three steps, the principles
(sources first, declines out of scope, two languages), a table of data
sources with their reuse terms, the labelling policy for generated content,
and a note on purpose: the archive is a demonstration of the pipeline, which
could index any field's public documents.

## Data sources

Primary corpus: the Federal Reserve Board (press conferences with transcripts
and captions, statements, minutes, speeches, reports) and the Bank of Japan
(press conference summaries and videos, statements, minutes, outlook reports,
speeches).

Additional feeds for the briefs and the latest list: European Central Bank
press releases, the BIS central bankers' speeches library, US Bureau of
Economic Analysis releases, Japan's Ministry of Finance and Financial Services
Agency release feeds. Bank of England items appear as title and link only,
because its terms do not permit republishing excerpts. Reuse terms for every
source are recorded in `docs/data-sources.md` as each ingestion module lands.

## Out of scope

Theme switching, user accounts, comments, following or watch lists,
date-range parameters on lists, social media posts, and any content that
exists only to fill a page before real data does.
