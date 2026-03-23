import csv
from datetime import datetime, time, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from labs.models import LabResult, Patient, Team

DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y")
UNIT_NORMALIZATION = {
    "mg/dl": "mg/dL",
    "mg/dL": "mg/dL",
    "MG/DL": "mg/dL",
}
TEST_NAME_SYNONYMS = {
    ("2339-0", "Glu"): "Glucose",
}
REQUIRED_FIELDS = ("mrn", "accession_number", "test_code", "collection_date")


class Command(BaseCommand):
    help = "Import Riverside Community lab results from a CSV export"

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str)
        parser.add_argument(
            "--team-slug",
            default="riverside-community",
            help="Team slug to import records into",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"]).expanduser()
        if not csv_path.exists():
            raise CommandError(f"CSV file does not exist: {csv_path}")

        team = Team.objects.filter(slug=options["team_slug"]).first()
        if not team:
            raise CommandError(f"Team not found for slug: {options['team_slug']}")

        with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            if not reader.fieldnames:
                raise CommandError("CSV file is missing a header row")

            stats = {
                "rows_read": 0,
                "patients_created": 0,
                "patients_updated": 0,
                "lab_results_created": 0,
                "lab_results_updated": 0,
                "errors": [],
            }
            normalized_rows = []

            for row_number, raw_row in enumerate(reader, start=2):
                stats["rows_read"] += 1
                try:
                    normalized_rows.append(
                        self._normalize_row(raw_row, row_number, csv_path.name)
                    )
                except Exception as exc:
                    stats["errors"].append(f"Row {row_number}: {exc}")

        preferred_patients = self._build_preferred_patients(normalized_rows)

        for normalized in normalized_rows:
            normalized["preferred_patient_name"] = preferred_patients[normalized["mrn"]][
                "patient_name"
            ]
            normalized["preferred_date_of_birth"] = preferred_patients[normalized["mrn"]][
                "date_of_birth"
            ]

            with transaction.atomic():
                patient, patient_created, patient_updated = self._upsert_patient(
                    team, normalized
                )
                if patient_created:
                    stats["patients_created"] += 1
                elif patient_updated:
                    stats["patients_updated"] += 1

                _, lab_created = LabResult.objects.update_or_create(
                    accession_number=normalized["accession_number"],
                    defaults={
                        "patient": patient,
                        "test_name": normalized["test_name"],
                        "test_code": normalized["test_code"],
                        "value": normalized["value"],
                        "unit": normalized["unit"],
                        "effective_date": normalized["effective_datetime"],
                        "observation_data": normalized["observation_data"],
                    },
                )
                if lab_created:
                    stats["lab_results_created"] += 1
                else:
                    stats["lab_results_updated"] += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Import complete: "
                f"{stats['rows_read']} rows read, "
                f"{stats['patients_created']} patients created, "
                f"{stats['patients_updated']} patients updated, "
                f"{stats['lab_results_created']} lab results created, "
                f"{stats['lab_results_updated']} lab results updated, "
                f"{len(stats['errors'])} errors"
            )
        )

        for error in stats["errors"]:
            self.stdout.write(self.style.WARNING(error))

    def _normalize_row(self, raw_row, row_number, source_file):
        row = {key: (value or "").strip() for key, value in raw_row.items()}

        missing_fields = [field for field in REQUIRED_FIELDS if not row.get(field)]
        if missing_fields:
            raise ValueError(f"missing required fields: {', '.join(missing_fields)}")

        date_of_birth = self._parse_date(row["date_of_birth"], "date_of_birth", row_number)
        collection_date = self._parse_date(
            row["collection_date"], "collection_date", row_number
        )
        effective_datetime = datetime.combine(
            collection_date, time.min, tzinfo=timezone.utc
        )

        unit = UNIT_NORMALIZATION.get(row["unit"], row["unit"])
        test_name = TEST_NAME_SYNONYMS.get(
            (row["test_code"], row["test_name"]), row["test_name"]
        )

        observation_data = {
            "resourceType": "Observation",
            "status": "final",
            "identifier": [{"value": row["accession_number"]}],
            "code": {
                "coding": [
                    {
                        "code": row["test_code"],
                        "display": test_name,
                    }
                ]
            },
            "subject": {"reference": f"Patient/{row['mrn']}"},
            "effectiveDateTime": effective_datetime.isoformat().replace("+00:00", "Z"),
            "valueString": row["value"],
            "source_type": "riverside_csv",
            "source_file": source_file,
            "raw_row": raw_row,
        }
        if unit:
            observation_data["valueQuantity"] = {
                "value": row["value"],
                "unit": unit,
            }

        return {
            "mrn": row["mrn"],
            "patient_name": row["patient_name"],
            "date_of_birth": date_of_birth,
            "test_name": test_name,
            "test_code": row["test_code"],
            "value": row["value"],
            "unit": unit,
            "accession_number": row["accession_number"],
            "effective_datetime": effective_datetime,
            "observation_data": observation_data,
            "source_file": source_file,
            "raw_row": raw_row,
        }

    def _upsert_patient(self, team, normalized):
        patient, created = Patient.objects.get_or_create(
            team=team,
            mrn=normalized["mrn"],
            defaults={
                "name": normalized["preferred_patient_name"],
                "date_of_birth": normalized["preferred_date_of_birth"],
                "patient_data": self._build_patient_data(normalized),
            },
        )

        updated = False
        if not created:
            if normalized["preferred_patient_name"] != patient.name:
                patient.name = normalized["preferred_patient_name"]
                updated = True
            if patient.date_of_birth != normalized["preferred_date_of_birth"]:
                patient.date_of_birth = normalized["preferred_date_of_birth"]
                updated = True

            patient_data = patient.patient_data if isinstance(patient.patient_data, dict) else {}
            merged_data = {
                **patient_data,
                **self._build_patient_data(normalized),
            }
            if merged_data != patient.patient_data:
                patient.patient_data = merged_data
                updated = True

            if updated:
                patient.save(update_fields=["name", "date_of_birth", "patient_data"])

        return patient, created, updated

    def _build_patient_data(self, normalized):
        return {
            "source_type": "riverside_csv",
            "source_file": normalized["source_file"],
            "csv_import": {
                "mrn": normalized["mrn"],
                "patient_name": normalized["preferred_patient_name"],
                "date_of_birth": normalized["preferred_date_of_birth"].isoformat(),
            },
        }

    def _build_preferred_patients(self, normalized_rows):
        preferred = {}
        for normalized in normalized_rows:
            current = preferred.get(normalized["mrn"])
            if not current:
                preferred[normalized["mrn"]] = {
                    "patient_name": normalized["patient_name"],
                    "date_of_birth": normalized["date_of_birth"],
                }
                continue

            if len(normalized["patient_name"]) > len(current["patient_name"]):
                current["patient_name"] = normalized["patient_name"]
            current["date_of_birth"] = normalized["date_of_birth"]

        return preferred

    def _parse_date(self, value, field_name, row_number):
        for date_format in DATE_FORMATS:
            try:
                return datetime.strptime(value, date_format).date()
            except ValueError:
                continue
        raise ValueError(
            f"invalid {field_name} '{value}' on row {row_number}; "
            f"expected one of: {', '.join(DATE_FORMATS)}"
        )
