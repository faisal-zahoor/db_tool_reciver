### DB Tool Reciver

Live receiver app for bulk migration

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app db_tool_reciver
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/db_tool_reciver
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit

### Bulk Receiver API

This app exposes two whitelisted methods for chunked migration writes:

- `db_tool_reciver.api.bulk_upsert`
- `db_tool_reciver.api.enqueue_bulk_upsert`

Both methods accept:

- `doctype`: target DocType on the live site
- `rows`: JSON array of row objects
- `key_field`: field used to detect existing records (default: `name`)
- `update_existing`: `1/0`

Optional extra safety:

Use standard Frappe token auth for access control.

Example call:

```bash
curl -X POST "https://your-live-site/api/method/db_tool_reciver.api.bulk_upsert" \
	-H "Authorization: token <api_key>:<api_secret>" \
	-H "Content-Type: application/json" \
	--data '{
		"doctype": "Patient Encounter",
		"key_field": "name",
		"update_existing": 1,
		"rows": [{"name": "ENC-0001", "status": "Open"}]
	}'
```
