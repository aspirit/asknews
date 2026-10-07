# Data sources

Every document in the archive comes from an official publisher and keeps
its original URL. Terms were checked when each source was added; the
`About` page shows this table to visitors in short form.

| Publisher | Material | Access | Terms |
| --- | --- | --- | --- |
| Federal Reserve Board (`fed`) | FOMC press conferences (video metadata, English captions repaired from the transcript PDF), FOMC statements, minutes, projection materials (Table 1 of the Summary of Economic Projections), Board speeches | FOMC calendar and historical pages, speech index pages, the player's public playback API | Website content is in the public domain as a U.S. government work; the Board asks to be cited as the source ([disclaimer](https://www.federalreserve.gov/disclaimer.htm)) |
| Bank of Japan (`boj`) | Press conference summaries (HTML since 2018, PDF before), statements and operational notices, minutes, Summary of Opinions, Outlook Report (basic view), speeches; press conference videos on the Bank's YouTube channel | Yearly index pages; the channel's feed or the YouTube Data API | Reproduction permitted with the source stated, not for commercial purposes, no alteration, photographs and images excluded ([copyright page](https://www.boj.or.jp/about/copyright.htm)) |
| European Central Bank (`ecb`) | Press releases | RSS, page text | Free use with the ECB cited as the source ([disclaimer](https://www.ecb.europa.eu/home/disclaimer/html/index.en.html)) |
| Bank for International Settlements (`bis`) | Central bankers' speeches | RSS descriptions only | Non-commercial redistribution and limited extracts with the BIS cited ([terms](https://www.bis.org/terms_conditions.htm)) |
| U.S. Bureau of Economic Analysis (`bea`) | Releases | RSS descriptions | Public domain as a U.S. government work |
| Ministry of Finance, Japan (`mof`) | Press conferences and policy releases (auction notices excluded) | RSS, page text | Public Data License 1.0: attribution, and processed content must say it was processed |
| Financial Services Agency, Japan (`fsa`) | Press conferences and releases | RSS, page text | Public Data License 1.0, as above |
| Bank of England (`boe`) | Titles and links only | RSS | Site terms allow personal and internal use only, so no excerpts are stored ([legal](https://www.bankofengland.co.uk/legal)) |

## How the terms are respected

- **Attribution** is structural: every list item, citation and document page carries the publisher's name and a link to the original.
- **No alteration:** official text is stored and shown as published. Generated summaries, translations and briefs are separate fields and carry a visible AI label.
- **Non-commercial:** the site carries no advertising and sells nothing.
- **Video** is never downloaded or re-hosted. Pages embed the publisher's own player (Brightcove for the Federal Reserve, YouTube for the Bank of Japan) and seek it from transcript timestamps.
- **Fetching** goes through one client with a descriptive user agent, per-host pacing and an on-disk cache, so a re-run does not hit the publishers again.
