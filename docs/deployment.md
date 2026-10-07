# Deployment

One host, Docker Compose, nginx in front. The stack is three containers:
PostgreSQL with pgvector, the embedding service, and the application (API,
pages and CLI). Everything below assumes Ubuntu with Docker and nginx
installed and a domain pointing at the host.

## Host requirements

| Component | Memory | Notes |
| --- | --- | --- |
| PostgreSQL | about 1 GB | 90k segments with 768-dimensional vectors fit in a few GB of disk |
| Embedding service | about 3 GB | EmbeddingGemma-300m on CPU; one worker |
| Application | about 300 MB | uvicorn, one process |

A 2 vCPU / 8 GB machine is comfortable. The initial backfill is network
and CPU bound and takes a few hours; the daily job takes minutes.

## First deployment

```sh
sudo mkdir -p /opt/asknews && sudo chown "$USER" /opt/asknews
git clone https://github.com/aspirit/asknews /opt/asknews && cd /opt/asknews
cp .env.example .env            # then set the production variables at the bottom
docker compose -f docker-compose.prod.yml up -d --build db embed
docker compose -f docker-compose.prod.yml run --rm app asknews migrate
docker compose -f docker-compose.prod.yml up -d app
```

Required variables in `.env`: `POSTGRES_PASSWORD`, `ASKNEWS_CLIENT_SALT`,
`ANTHROPIC_API_KEY`. The embedding service runs `ASKNEWS_EMBED_MODEL`:
EmbeddingGemma (the default) is gated on Hugging Face, so its first start
needs `HF_TOKEN` from an account that accepted the model's terms;
`intfloat/multilingual-e5-base` has open weights and needs no token. After
the first download the weights live in the `hfcache` volume. Changing the
model means re-embedding: `asknews embed` replaces vectors whose model name
differs, and retrieval only compares vectors of the model in use.

## Loading the archive

Run the backfill once, in this order. Each command is idempotent and
resumes from the on-disk cache under the `appdata` volume if interrupted.

```sh
alias ask='docker compose -f docker-compose.prod.yml run --rm app asknews'
ask ingest fed-presconf            # 94 press conferences, captions and transcripts
ask ingest fed-releases            # statements, minutes, speeches since 2011
ask ingest boj-kaiken              # press conference summaries since 2011
ask ingest boj-releases            # statements, minutes, opinions, outlook, speeches
ask ingest boj-videos              # attach YouTube videos (feed: latest only; API key: all)
ask ingest feeds --days 10         # ECB, BIS, BEA, MOF, FSA, BoE
ask topics                         # topic membership for the rankings
ask embed                          # all segments through the embedding service
ask briefs meetings --limit 20     # meeting briefs, newest first
ask briefs daily --days 7
ask summarize --limit 200          # repeat until it reports 0 documents
ask eval                           # retrieval regression cases should pass
```

## Web server and TLS

```sh
sudo cp deploy/nginx-asknews.conf /etc/nginx/sites-available/asknews
sudo ln -s /etc/nginx/sites-available/asknews /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d asknews.jp -d www.asknews.jp
```

Certbot rewrites the server block for HTTPS and installs its own renewal
timer. The application trusts `X-Forwarded-*` headers from the proxy
(`--proxy-headers` in the image's command), which is what the daily
client hash for rate limiting is derived from.

## Daily job

```sh
sudo cp deploy/asknews-daily.service deploy/asknews-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now asknews-daily.timer
systemctl list-timers asknews-daily.timer
```

The job fetches this year's publications from every source, tags and embeds
new documents, writes briefs for the last three days and for up to three
meetings, and summarises up to forty documents. Its log is in
`journalctl -u asknews-daily.service`.

## Operations

- **Logs:** `docker compose -f docker-compose.prod.yml logs -f app`.
- **Backup:** `docker compose -f docker-compose.prod.yml exec db pg_dump -U asknews asknews | gzip > asknews-$(date +%F).sql.gz`.
- **Upgrade:** `git pull && docker compose -f docker-compose.prod.yml up -d --build && ... run --rm app asknews migrate`.
- **Spend:** `ask_requests` and `llm_usage` record tokens and the estimated cost of every model call; the daily cap (`ASKNEWS_DAILY_BUDGET_USD`, any 24 hours) stops generation at half of it and new answers at all of it, and the site says so instead of failing.
- **Model unavailable:** without a valid `ANTHROPIC_API_KEY` the Ask page still retrieves and shows sources, and reports that the answer engine did not respond.
