# Putting the collector on a server

The single most valuable thing you can do right now. Every measurement in this
repository is limited by having roughly ten hours of data instead of weeks, and
that is purely because nothing has been running continuously.

Budget: **$5–7/month**. Time: about ten minutes.

---

## 1. Get a server

Any of these work. Pick the cheapest small instance; the collector uses well
under 1 GB of RAM and almost no CPU.

| provider | plan | ~cost |
|---|---|---|
| Hetzner | CX22 | €4.5/mo |
| DigitalOcean | Basic droplet 1 GB | $6/mo |
| Vultr | Regular 1 GB | $6/mo |
| Linode | Nanode 1 GB | $5/mo |

Choose **Ubuntu 24.04** (22.04 is fine too). Region does not matter for
collecting — pick anything near you. It matters only if you later trade live,
where you would want Frankfurt, Amsterdam, or a US east location near validator
concentration.

## 2. Run one command

SSH in and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/xin10ylop/memecoin2/claude/memecoin-trading-bot-y7skua/deploy/bootstrap.sh | sudo bash
```

That installs Python and the code, creates an unprivileged `degen` user, and
registers the collector as a systemd service that starts on boot and restarts
itself if it ever dies. It is safe to run again later to update.

## 3. Confirm it is alive

```bash
sudo -u degen /opt/degen/.venv/bin/degen doctor
```

Every required line should read `ok`. Then watch it work:

```bash
journalctl -u degen-collector -f
```

You want lines like `discovered=1839 (1751/h) snapshots=65088`. Ctrl-C to stop
watching — that does not stop the service.

## 4. Leave it alone

Genuinely. Check in weekly:

```bash
sudo -u degen /opt/degen/.venv/bin/degen status
```

You are waiting for **100,000+ tracked mints across at least two weeks**. At the
observed rate of roughly 1,800 launches an hour that takes about three weeks.

## 5. Then measure

```bash
sudo -u degen /opt/degen/.venv/bin/degen validate
```

Read the out-of-sample column and ignore the in-sample one. If out-of-sample is
still positive, continue to paper trading. If it is not, you have saved yourself
the money, which is a good outcome.

---

## Everyday commands

```bash
systemctl status degen-collector     # is it running
systemctl restart degen-collector    # restart it
systemctl stop degen-collector       # stop it
journalctl -u degen-collector -f     # live log
sudo bash /opt/degen/deploy/bootstrap.sh   # pull the latest code and restart
```

Logs rotate daily and keep 14 days. Data lives in `/opt/degen/data` — that
directory *is* the asset, so if you rebuild the server, copy it first.

## Disk

Roughly **250 MB per million snapshots**, so about 1–2 GB a month. Any of the
plans above has room for a year. To reclaim space from closed days:

```python
from degen.store.lake import lake
lake().compact("snapshots")   # folds finished days into compressed Parquet
```

## Things that go wrong

| symptom | cause | fix |
|---|---|---|
| `FAIL` on a data source in `doctor` | provider outage or IP-blocked | wait, then re-check; if persistent the provider has blocked the datacentre range and you need a different host |
| lots of `429` in the log | rate limited | harmless, the client backs off; only act if `err` climbs past ~10% of `ok` |
| service keeps restarting | check `journalctl -u degen-collector -n 50` | usually a bad `.env` |
| disk full | old logs or an uncompacted lake | `logrotate -f /etc/logrotate.d/degen`, then compact |

## If you later trade live

Collecting needs nothing. Live trading needs two additions to `/opt/degen/.env`:

- `DEGEN_RPC_URL` — a paid endpoint. The public one will cost you fills. Helius,
  Triton, QuickNode and Chainstack all work; expect ~$50/month.
- `DEGEN_WALLET_KEY` — a base58 secret key for a **dedicated** wallet holding
  only what you are prepared to lose. Never your main wallet. The file is
  already `chmod 600` and owned by the service user.

Do not skip the paper-trading month in `docs/RUNBOOK.md`.
