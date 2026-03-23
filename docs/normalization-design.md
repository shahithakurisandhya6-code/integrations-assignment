# Riverside Community CSV Normalization Plan

## Goal

Load `data/riverside_community_labs.csv` into the existing Django data model for the `Riverside Community` team:

- `Patient`
- `LabResult`

The CSV does not include allergy data, so this pipeline will only create or update patients and lab results.

## Source Inspection Summary

The file contains 45 lab-result rows with these columns:

- `patient_name`
- `mrn`
- `date_of_birth`
- `test_name`
- `test_code`
- `value`
- `unit`
- `collection_date`
- `accession_number`

Observed normalization issues in the file:

- Mixed date formats:
  - `MM/DD/YYYY`
  - `YYYY-MM-DD`
- Unit inconsistencies:
  - `mg/dL`
  - `mg/dl`
  - `MG/DL`
  - one blank unit
- Patient name drift for the same MRN:
  - `RC-002` appears as both `Rodriguez, Maria` and `Rodriguez, Maria L.`
- Test name drift for the same code:
  - code `2339-0` appears as both `Glucose` and `Glu`
- Non-simple result value:
  - one glucose result is `>200`

There is also a data alignment concern with the seeded demo database: the existing Riverside patients in `seed_data.py` do not fully match the CSV roster. Because of that, the importer should upsert patients by `(team, mrn)` and allow CSV demographics to replace stale seeded demo values.

## Mapping to the Current Model

### Patient

CSV to `Patient`:

- `mrn` -> `Patient.mrn`
- `patient_name` -> `Patient.name`
- `date_of_birth` -> `Patient.date_of_birth`
- raw row-derived metadata -> `Patient.patient_data`

Patient identity should be based on:

- `team = Riverside Community`
- `mrn`

That matches the existing uniqueness rule on `Patient` (`unique_together = ["team", "mrn"]`).

### LabResult

CSV to `LabResult`:

- `accession_number` -> `LabResult.accession_number`
- `test_name` -> `LabResult.test_name`
- `test_code` -> `LabResult.test_code`
- `value` -> `LabResult.value`
- normalized `unit` -> `LabResult.unit`
- parsed `collection_date` -> `LabResult.effective_date`
- row-derived metadata -> `LabResult.observation_data`

`accession_number` is the best natural key for upserting lab results because it already exists in the model and appears unique in the file.

## Normalization Decisions

### 1. Team resolution

The importer should resolve the `Riverside Community` team by slug (`riverside-community`) and fail clearly if it does not exist.

### 2. Patient upsert strategy

Use `update_or_create(team=team, mrn=row["mrn"], defaults=...)`.

Reasoning:

- it is idempotent for reruns
- it avoids duplicate patients
- it lets us correct seeded demo data when the CSV is more current

### 3. Name selection

Store the most complete name seen for a given MRN during the import run.

Practical rule:

- prefer the longer non-empty `patient_name` value for the patient record

Example:

- keep `Rodriguez, Maria L.` over `Rodriguez, Maria`

### 4. Date parsing

Parse `date_of_birth` and `collection_date` using a small accepted-format list:

- `%m/%d/%Y`
- `%Y-%m-%d`
- `%m/%d/%y`

For `collection_date`, the CSV only provides a date, not a time. To fit the existing `DateTimeField`, store the parsed date at midnight UTC.

This is not perfect clinically, but it is deterministic and honest about the source precision. The original CSV value should also be preserved in `observation_data`.

### 5. Unit normalization

Normalize obvious casing-only variants:

- `mg/dl` -> `mg/dL`
- `MG/DL` -> `mg/dL`

Keep already valid units unchanged.

If `unit` is blank, store `""` in `LabResult.unit` and preserve the raw CSV value in `observation_data`.

I do not want to infer a missing unit from the test code on the first pass, because that hides source-data quality issues.

### 6. Test naming

Keep the CSV `test_code` as authoritative, but normalize the display name when there is a known synonym.

Initial synonym handling:

- if `test_code == "2339-0"` and `test_name == "Glu"`, store `Glucose`

This keeps the dashboard display cleaner without losing the original row value, which should still be saved in `observation_data`.

### 7. Result values

Store `value` as a string exactly as received after trimming whitespace.

Reasoning:

- the current model uses `CharField`
- values like `>200` are clinically meaningful and should not be coerced to a float

### 8. Lab-result upsert strategy

Use `update_or_create(accession_number=..., defaults=...)`.

Reasoning:

- rerunning the import should not duplicate results
- the file appears to treat accession number as a unique row identifier
- later corrected CSV deliveries can overwrite stale values

## Proposed Import Shape

Implement a dedicated management command for CSV ingestion.

Suggested command:

`python manage.py import_riverside_csv ../data/riverside_community_labs.csv`

High-level flow:

1. Resolve the Riverside team.
2. Read CSV rows with `csv.DictReader`.
3. Normalize each row into a canonical intermediate dict.
4. Upsert the patient.
5. Upsert the lab result tied to that patient.
6. Track counts and row-level validation errors.
7. Print a concise import summary.

## Metadata to Preserve

Even after normalization, preserve the original source row in `LabResult.observation_data` and useful import metadata in `Patient.patient_data` / `LabResult.observation_data`.

Suggested metadata fields:

- `source_type = "riverside_csv"`
- `source_file = "riverside_community_labs.csv"`
- `raw_row`
- normalized parsing details when helpful

That will make debugging much easier if a user later questions a result.

## Validation and Error Handling

Rows should be rejected with a clear error if they are missing:

- `mrn`
- `accession_number`
- `test_code`
- `collection_date`

Prefer collecting row-level errors and reporting them at the end instead of aborting the entire import on the first bad row.

For this assignment, I would treat the current file as importable with normalization rather than failing it for the known inconsistencies above.

## Implementation Notes

I expect the importer to reuse the current local model shape directly rather than forcing the CSV through the FHIR utility layer. The FHIR parser is useful for bundle ingestion, but this CSV source is simpler and has different normalization needs.

After implementation, validation should include:

- rerun safety: importing twice does not duplicate patients or lab results
- correct patient counts under the Riverside team
- correct handling of `>200`
- correct normalization of mixed date formats
- correct normalization of `mg/dl` / `MG/DL`
- correct preservation of blank units and raw source data
