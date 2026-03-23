import csv
import io
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from django.core.management import call_command
from labs.models import LabResult, Patient


@pytest.mark.django_db
class TestPatientListAPI:
    def test_returns_patients(self, api_client, patients):
        response = api_client.get("/api/teams/lakewood-memorial/patients/")
        assert response.status_code == 200
        assert len(response.data["results"]) > 0

    def test_scopes_patients_to_team(self, api_client, patients):
        response = api_client.get("/api/teams/lakewood-memorial/patients/")
        mrns = {patient["mrn"] for patient in response.data["results"]}
        assert mrns == {"LM-001", "LM-002"}

    def test_includes_patient_fields(self, api_client, patients):
        response = api_client.get("/api/teams/lakewood-memorial/patients/")
        patient = response.data["results"][0]
        assert "name" in patient
        assert "mrn" in patient
        assert "date_of_birth" in patient
        assert "team_name" in patient


@pytest.mark.django_db
class TestPatientDetailAPI:
    def test_returns_patient(self, api_client, patients):
        patient = patients["lw1"]
        response = api_client.get(
            f"/api/teams/lakewood-memorial/patients/{patient.id}/"
        )
        assert response.status_code == 200
        assert response.data["name"] == "Chen, David"
        assert response.data["mrn"] == "LM-001"

    def test_includes_allergies(self, api_client, patients, allergies):
        patient = patients["lw1"]
        response = api_client.get(
            f"/api/teams/lakewood-memorial/patients/{patient.id}/"
        )
        assert response.status_code == 200
        assert len(response.data["allergies"]) == 2

    def test_includes_allergies_without_criticality(self, api_client, patients):
        from labs.models import PatientAllergy

        patient = patients["lw2"]
        PatientAllergy.objects.create(
            patient=patient,
            substance="Penicillin",
            criticality=None,
            allergy_data={
                "resourceType": "AllergyIntolerance",
                "code": {"coding": [{"display": "Penicillin"}]},
            },
        )

        response = api_client.get(
            f"/api/teams/lakewood-memorial/patients/{patient.id}/"
        )
        assert response.status_code == 200
        assert response.data["allergies"] == [
            {
                "id": response.data["allergies"][0]["id"],
                "substance": "Penicillin",
                "criticality": None,
                "allergy_data": {
                    "resourceType": "AllergyIntolerance",
                    "code": {"coding": [{"display": "Penicillin"}]},
                },
            }
        ]

    def test_returns_404_for_patient_on_wrong_team(self, api_client, patients):
        patient = patients["rv1"]
        response = api_client.get(
            f"/api/teams/lakewood-memorial/patients/{patient.id}/"
        )
        assert response.status_code == 404

    def test_includes_lab_results(self, api_client, patients, lab_results):
        patient = patients["lw1"]
        response = api_client.get(
            f"/api/teams/lakewood-memorial/patients/{patient.id}/"
        )
        assert response.status_code == 200
        assert len(response.data["lab_results"]) == 3


@pytest.mark.django_db
class TestLabResultAPI:
    def test_returns_lab_results(self, api_client, patients, lab_results):
        patient = patients["lw1"]
        response = api_client.get(
            f"/api/teams/lakewood-memorial/patients/{patient.id}/lab-results/"
        )
        assert response.status_code == 200
        assert len(response.data["results"]) == 3

    def test_lab_result_fields(self, api_client, patients, lab_results):
        patient = patients["lw1"]
        response = api_client.get(
            f"/api/teams/lakewood-memorial/patients/{patient.id}/lab-results/"
        )
        result = response.data["results"][0]
        assert "test_name" in result
        assert "value" in result
        assert "unit" in result
        assert "effective_date" in result
        assert "observation_data" in result

    def test_returns_404_for_lab_results_on_wrong_team(self, api_client, patients, lab_results):
        patient = patients["lw1"]
        response = api_client.get(
            f"/api/teams/riverside-community/patients/{patient.id}/lab-results/"
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestFHIRIngestion:
    def test_process_fhir_bundle(self, teams):
        from labs.fhir import process_fhir_bundle

        bundle = {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "test-pt",
                        "identifier": [
                            {"type": {"coding": [{"code": "MR"}]}, "value": "TEST-001"}
                        ],
                        "name": [{"family": "Test", "given": ["Patient"]}],
                        "birthDate": "1990-01-01",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "test-obs",
                        "status": "final",
                        "code": {
                            "coding": [
                                {"system": "http://loinc.org", "code": "2339-0", "display": "Glucose"}
                            ]
                        },
                        "subject": {"reference": "Patient/TEST-001"},
                        "effectiveDateTime": "2026-03-10T09:00:00Z",
                        "valueQuantity": {"value": 95, "unit": "mg/dL"},
                        "identifier": [{"value": "ACC-001"}],
                    }
                },
            ],
        }

        result = process_fhir_bundle(bundle, teams["lakewood"])
        assert result["patients"] == 1
        assert result["observations"] == 1
        assert Patient.objects.filter(mrn="TEST-001").exists()
        assert LabResult.objects.filter(accession_number="ACC-001").exists()

    def test_processes_observation_effective_period(self, teams):
        from labs.fhir import process_fhir_bundle

        bundle = {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "identifier": [
                            {"type": {"coding": [{"code": "MR"}]}, "value": "TEST-002"}
                        ],
                        "name": [{"family": "Period", "given": ["Patient"]}],
                        "birthDate": "1990-01-01",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "test-obs-period",
                        "status": "final",
                        "code": {
                            "coding": [
                                {"system": "http://loinc.org", "code": "2339-0", "display": "Glucose"}
                            ]
                        },
                        "subject": {"reference": "Patient/TEST-002"},
                        "effectivePeriod": {
                            "start": "2026-03-10T09:00:00Z",
                            "end": "2026-03-10T09:15:00Z",
                        },
                        "valueQuantity": {"value": 95, "unit": "mg/dL"},
                        "identifier": [{"value": "ACC-002"}],
                    }
                },
            ],
        }

        result = process_fhir_bundle(bundle, teams["lakewood"])
        assert result["observations"] == 1
        assert LabResult.objects.filter(accession_number="ACC-002").exists()

    def test_processes_observation_without_timezone_suffix(self, teams):
        from labs.fhir import process_fhir_bundle

        bundle = {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "identifier": [
                            {"type": {"coding": [{"code": "MR"}]}, "value": "TEST-003"}
                        ],
                        "name": [{"family": "Timezone", "given": ["Patient"]}],
                        "birthDate": "1990-01-01",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "test-obs-no-tz",
                        "status": "final",
                        "code": {
                            "coding": [
                                {"system": "http://loinc.org", "code": "2339-0", "display": "Glucose"}
                            ]
                        },
                        "subject": {"reference": "Patient/TEST-003"},
                        "effectiveDateTime": "2026-03-10T09:00:00",
                        "valueQuantity": {"value": 95, "unit": "mg/dL"},
                        "identifier": [{"value": "ACC-003"}],
                    }
                },
            ],
        }

        result = process_fhir_bundle(bundle, teams["lakewood"])
        assert result["observations"] == 1
        stored = LabResult.objects.get(accession_number="ACC-003")
        assert stored.effective_date.isoformat() == "2026-03-10T09:00:00+00:00"


@pytest.mark.django_db
class TestRiversideCSVImport:
    def test_import_normalizes_rows_and_upserts_patients(self, tmp_path, teams):
        csv_path = tmp_path / "riverside.csv"
        rows = [
            {
                "patient_name": "Rodriguez, Maria",
                "mrn": "RC-002",
                "date_of_birth": "03/21/1990",
                "test_name": "Glucose",
                "test_code": "2339-0",
                "value": "105",
                "unit": "mg/dL",
                "collection_date": "03/05/2026",
                "accession_number": "RC-100",
            },
            {
                "patient_name": "Rodriguez, Maria L.",
                "mrn": "RC-002",
                "date_of_birth": "03/21/1990",
                "test_name": "Glu",
                "test_code": "2339-0",
                "value": ">200",
                "unit": "MG/DL",
                "collection_date": "2026-03-12",
                "accession_number": "RC-101",
            },
            {
                "patient_name": "Thompson, James",
                "mrn": "RC-003",
                "date_of_birth": "11/03/1973",
                "test_name": "Sodium",
                "test_code": "2951-2",
                "value": "142",
                "unit": "",
                "collection_date": "03/09/2026",
                "accession_number": "RC-102",
            },
        ]
        self._write_csv(csv_path, rows)

        out = io.StringIO()
        call_command("import_riverside_csv", str(csv_path), stdout=out)

        patient = Patient.objects.get(team=teams["riverside"], mrn="RC-002")
        assert patient.name == "Rodriguez, Maria L."
        assert patient.date_of_birth.isoformat() == "1990-03-21"
        assert patient.patient_data["source_type"] == "riverside_csv"

        glucose = LabResult.objects.get(accession_number="RC-101")
        assert glucose.patient == patient
        assert glucose.test_name == "Glucose"
        assert glucose.value == ">200"
        assert glucose.unit == "mg/dL"
        assert glucose.effective_date.isoformat() == "2026-03-12T00:00:00+00:00"
        assert glucose.observation_data["effectiveDateTime"] == "2026-03-12T00:00:00Z"
        assert glucose.observation_data["raw_row"]["test_name"] == "Glu"

        sodium = LabResult.objects.get(accession_number="RC-102")
        assert sodium.unit == ""
        assert "valueQuantity" not in sodium.observation_data

        assert "2 patients created" in out.getvalue()
        assert "3 lab results created" in out.getvalue()

    def test_import_is_idempotent(self, tmp_path, teams):
        csv_path = tmp_path / "riverside.csv"
        rows = [
            {
                "patient_name": "Kim, Sarah",
                "mrn": "RC-004",
                "date_of_birth": "08/09/1995",
                "test_name": "Glucose",
                "test_code": "2339-0",
                "value": "78",
                "unit": "mg/dL",
                "collection_date": "2026-03-03",
                "accession_number": "RC-200",
            }
        ]
        self._write_csv(csv_path, rows)

        call_command("import_riverside_csv", str(csv_path))
        call_command("import_riverside_csv", str(csv_path))

        assert Patient.objects.filter(team=teams["riverside"], mrn="RC-004").count() == 1
        assert LabResult.objects.filter(accession_number="RC-200").count() == 1

    def test_import_accepts_two_digit_year_collection_dates(self, tmp_path, teams):
        csv_path = tmp_path / "riverside.csv"
        rows = [
            {
                "patient_name": "Thompson, James",
                "mrn": "RC-003",
                "date_of_birth": "11/03/1973",
                "test_name": "Glu",
                "test_code": "2339-0",
                "value": "91",
                "unit": "mg/dL",
                "collection_date": "3/9/26",
                "accession_number": "RC-202",
            }
        ]
        self._write_csv(csv_path, rows)

        call_command("import_riverside_csv", str(csv_path))

        lab_result = LabResult.objects.get(accession_number="RC-202")
        assert lab_result.test_name == "Glucose"
        assert lab_result.effective_date.isoformat() == "2026-03-09T00:00:00+00:00"

    def test_import_overwrites_seeded_demo_demographics_with_csv_values(self, tmp_path, teams):
        Patient.objects.create(
            team=teams["riverside"],
            mrn="RC-004",
            name="Okafor, Emmanuel",
            date_of_birth=date(1960, 12, 2),
            patient_data={"resourceType": "Patient", "id": "RC-004"},
        )

        csv_path = tmp_path / "riverside.csv"
        rows = [
            {
                "patient_name": "Kim, Sarah",
                "mrn": "RC-004",
                "date_of_birth": "08/09/1995",
                "test_name": "Glucose",
                "test_code": "2339-0",
                "value": "78",
                "unit": "mg/dL",
                "collection_date": "2026-03-03",
                "accession_number": "RC-203",
            }
        ]
        self._write_csv(csv_path, rows)

        call_command("import_riverside_csv", str(csv_path))

        patient = Patient.objects.get(team=teams["riverside"], mrn="RC-004")
        assert patient.name == "Kim, Sarah"
        assert patient.date_of_birth.isoformat() == "1995-08-09"
        assert patient.patient_data["source_type"] == "riverside_csv"

    def test_import_skips_invalid_rows_and_continues(self, tmp_path, teams):
        csv_path = tmp_path / "riverside.csv"
        rows = [
            {
                "patient_name": "Kim, Sarah",
                "mrn": "RC-004",
                "date_of_birth": "08/09/1995",
                "test_name": "Glucose",
                "test_code": "2339-0",
                "value": "78",
                "unit": "mg/dL",
                "collection_date": "2026-03-03",
                "accession_number": "",
            },
            {
                "patient_name": "Okafor, Emmanuel",
                "mrn": "RC-005",
                "date_of_birth": "12/02/1960",
                "test_name": "ALT",
                "test_code": "1742-6",
                "value": "38",
                "unit": "U/L",
                "collection_date": "03/11/2026",
                "accession_number": "RC-201",
            },
        ]
        self._write_csv(csv_path, rows)

        out = io.StringIO()
        call_command("import_riverside_csv", str(csv_path), stdout=out)

        assert not Patient.objects.filter(team=teams["riverside"], mrn="RC-004").exists()
        assert Patient.objects.filter(team=teams["riverside"], mrn="RC-005").exists()
        assert LabResult.objects.filter(accession_number="RC-201").exists()
        assert "1 errors" in out.getvalue()
        assert "missing required fields: accession_number" in out.getvalue()

    def _write_csv(self, path: Path, rows):
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=[
                    "patient_name",
                    "mrn",
                    "date_of_birth",
                    "test_name",
                    "test_code",
                    "value",
                    "unit",
                    "collection_date",
                    "accession_number",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
