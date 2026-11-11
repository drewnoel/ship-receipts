# Examples (synthetic)

Everything here is invented for illustration: no real people, companies, or numbers.

- `ideas.sample.json`: five ideas in the shape `receipts.py add` expects (receipt, why_now, exactly 3 questions, scores).
- `posted.sample.json`: a posted-log entry.
- `analytics.sample.csv`: the export shape `receipts.py debrief import` reads (`post` or `url`, plus metrics).

Try it in a scratch data dir:

```sh
export SHIP_RECEIPTS_HOME=$(mktemp -d)
python3 tools/receipts.py init
python3 tools/receipts.py add examples/ideas.sample.json
python3 tools/receipts.py queue list
python3 tools/receipts.py cadence status
python3 tools/receipts.py loop check
```
