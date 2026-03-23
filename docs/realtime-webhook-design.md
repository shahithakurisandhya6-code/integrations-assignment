# Riverside Real-Time Webhook Design

## Context

Today this project loads lab data either from seed data or from Riverside's CSV export. The frontend is already wired to read patient detail plus embedded lab results from the existing API, so I do not want to redesign the read path if I can avoid it. The simplest thing operationally is to keep `Patient` and `LabResult` as the tables the dashboard reads from and make the webhook flow write into that same shape.

There is also already a basic FHIR ingestion utility in `backend/labs/fhir.py`. Reading that code was useful because it shows the intended mapping, but it also shows the gaps I would want to close before putting a webhook in production:

- it does `update_or_create(accession_number=...)`, so corrected results would silently overwrite the old value with no history
- it assumes `Observation.subject.reference` can be split and used as the local patient MRN
- it processes the whole bundle inline instead of separating receipt from background processing

Those shortcuts are reasonable for the starter app, but they are also exactly the places where a real webhook flow would get into trouble.

Riverside now wants to send FHIR R4 Bundles by webhook as lab results are finalized. Volume is modest, around 5,000 results/day in bundles of 1 to 50 results, but the operational constraints are real:

- the endpoint has to respond within 3 seconds
- results need to show up in the dashboard within 5 minutes
- the hospital may retry the same payload on timeout
- corrections are common and are not the same thing as duplicates

The sample payload in `data/sample_webhook_payload.json` is a good example of the last point: it includes a corrected glucose result for the same accession number, not just a replay of the original message.

## Proposed approach

I would split the flow into two stages:

1. a very thin Django webhook endpoint that authenticates the request, stores the raw payload, and enqueues background work
2. a Celery worker that parses the Bundle and applies patient/result updates to PostgreSQL

That keeps the request path short enough to safely return within the hospital's 3-second timeout, while still giving us durable receipt of the payload before we acknowledge it. It also lets us keep the existing API shape intact, which matters here because the current read endpoints are simple and already working.

The main design choice here is to treat webhook receipt and FHIR processing as separate concerns. If we try to parse and upsert everything in the request itself, we are much more likely to hit timeouts, especially once we onboard more hospitals or start getting retry bursts during an incident.

## Request path

I would add an endpoint like:

`POST /api/webhooks/fhir/<hospital_slug>/`

Right now `backend/config/urls.py` only mounts the existing labs API under `/api/`, so this would be a net-new route rather than an extension of an existing webhook module.

On receipt, Django should:

- authenticate the sender
- verify that the payload is valid JSON and `resourceType = Bundle`
- look up the configured hospital/integration
- compute an idempotency key for the delivery
- store the raw payload in a `WebhookEvent` row
- enqueue a Celery task with the event ID
- return `202 Accepted`

That is the entire request path. I would keep resource-by-resource FHIR processing out of the request entirely.

For authentication, I would prefer an HMAC signature header if Riverside supports it. If not, a shared secret header is still better than an open endpoint. Either way, I would keep the auth details on a per-hospital integration config so we do not hardcode Riverside assumptions into the view.

## Persistence model

I would add two tables, instead of trying to stretch `LabResult.observation_data` into carrying all webhook state by itself.

### 1. `WebhookEvent`

This is the durable inbox for raw webhook deliveries.

Suggested fields:

- integration/hospital reference
- delivery ID from headers if the sender provides one
- idempotency key
- payload JSON
- payload hash
- status: `received`, `processing`, `processed`, `duplicate`, `failed`
- received timestamp
- processed timestamp
- attempt count
- last error

Unique key:

- `(integration_id, idempotency_key)`

If Riverside does not send a delivery ID, I would derive the idempotency key from a hash of the raw request body.

This gives us a clean answer to duplicate delivery: if the same payload is retried, we can accept it again without doing the work twice.

### 2. `ObservationVersion` (or similar)

The current `LabResult` table is a current-state table. That is still useful for the dashboard, but it is not enough by itself once corrections matter. This repo already uses JSON fields like `patient_data` and `observation_data` to preserve raw source material, so I would keep that pattern here, just with one more explicit layer for version history instead of shoving every correction into one JSON blob.

I would keep `LabResult` as the current state and add an append-only history table for each received Observation version. Suggested fields:

- foreign key to `LabResult`
- source observation ID
- accession number
- observation status
- issued timestamp
- raw observation payload
- received-at timestamp

This matters because a correction is not a duplicate. In the sample payload, the accession number stays the same, but the result changes from 95 to 98 and the status becomes `corrected`. If we reused the current `update_or_create(accession_number=...)` pattern from `fhir.py` unchanged, the latest value would win, but we would lose the audit trail of what changed.

## Processing path

The Celery worker should load a `WebhookEvent`, lock it for processing, and then:

1. mark the event as `processing`
2. iterate through Bundle entries
3. normalize patient identifiers
4. normalize Observation resources
5. upsert the current `LabResult`
6. write an `ObservationVersion` row
7. mark the event as `processed` or `failed`

I would put webhook ingestion on its own Celery queue. Riverside alone is not high volume, but isolating the queue avoids a situation where some unrelated background job delays lab-result visibility and quietly breaks the 5-minute requirement.

I would also keep the processing output targeted at the current tables the UI already expects. The current patient detail endpoint serializes `lab_results` directly off the patient model, so if webhook ingestion writes the same normalized rows, the frontend does not need to care whether a result came from seed data, CSV import, or webhook.

## Duplicate vs corrected result handling

This is the part I would be most careful about, because it is easy to get something that looks idempotent but is actually wrong for corrections.

### Duplicate delivery

This is an event-level problem. The same HTTP payload may arrive more than once because the hospital timed out waiting for our response.

My approach:

- dedupe at the `WebhookEvent` layer using the idempotency key
- if we have already recorded that same delivery, do not enqueue another full processing pass

### Corrected result

This is a clinical-data problem. A later Observation may intentionally replace an earlier one for the same accession number.

My approach:

- use accession number as the business key for the current `LabResult`
- use Observation `id`, `status`, and `issued` as version metadata
- write every new version to `ObservationVersion`
- update the current `LabResult` row only when the incoming version is newer

One repo-specific note here: `LabResult.accession_number` is not currently declared unique at the model level even though both the FHIR path and the CSV importer are treating it like a natural key. If we build the webhook path on the same assumption, I would add a uniqueness constraint or at least a scoped unique index as part of this work so the database matches the ingestion logic.

For version ordering, I would use:

1. `issued` timestamp when present
2. otherwise Bundle timestamp
3. otherwise webhook receipt time

That ordering rule should be explicit in code, because corrections are normal and another engineer will need to know why one payload won over another.

## Patient resolution

The existing parser in `fhir.py` assumes `Observation.subject.reference` maps directly to the local patient MRN. I would not rely on that for the webhook design.

The sample payload uses references like `Patient/pat-riverside-001`, which do not look like the CSV MRNs. Because of that, I would introduce hospital-scoped patient identifier mapping rather than assuming every partner sends the same identifier format.

That mismatch is easy to miss if you only look at the CSV import, because the CSV path keys patients by `team + mrn` and works fine. The webhook path needs one more layer of identifier resolution than the CSV importer did.

At minimum, the worker should:

- look for MRN-style identifiers on the Patient resource when available
- otherwise store and resolve a hospital-specific external patient ID
- always scope lookup by hospital/team

This is important for future hospitals too. Identifier handling is one of the first things that stops being reusable if we bake current assumptions into the model.

## Observability

Ops should be able to tell, without digging through raw logs, whether webhook ingestion is healthy.

I would add:

- structured logs with hospital slug, webhook event ID, number of Bundle entries, status, and processing latency
- metrics for:
  - webhook requests received
  - duplicate deliveries
  - processing successes and failures
  - queue lag
  - end-to-end latency from receipt to `LabResult` update
  - number of corrected results applied
- alerts for:
  - repeated processing failures
  - queue lag approaching 5 minutes
  - no successful Riverside ingests during an expected period

I would also add a lightweight internal admin page or Django admin model view for recent `WebhookEvent` rows. This project already exposes Django admin, so that is probably the fastest way to give ops something useful during rollout without inventing a whole separate internal UI.

## Failure handling

If the request is valid enough to store, I would store it and ack it even if downstream processing later fails. After that:

- retry transient Celery failures with exponential backoff
- do not endlessly retry permanent schema/validation failures
- keep failed payloads in PostgreSQL for replay

Replay should work by requeueing the stored `WebhookEvent`, not by asking Riverside to resend data.

## Why this fits the requirements

- returning `202` after storing the event keeps the request comfortably under 3 seconds
- Celery processing plus a dedicated queue gives us a realistic path to the 5-minute dashboard SLA
- `WebhookEvent` handles duplicate deliveries safely
- `ObservationVersion` lets us treat corrections as first-class updates instead of accidental overwrites
- hospital-scoped integration config and identifier mapping make the design reusable for future partners without rewriting the existing patient and lab-result read APIs

If I were implementing this next, I would probably build it in this order: durable receipt first, then async processing, then correction history, then better observability once the core path is stable.
