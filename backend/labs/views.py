from rest_framework import generics

from .models import LabResult, Patient
from .serializers import (
    LabResultSerializer,
    PatientDetailSerializer,
    PatientListSerializer,
)


class PatientListView(generics.ListAPIView):
    serializer_class = PatientListSerializer
    queryset = Patient.objects.all()


class PatientDetailView(generics.RetrieveAPIView):
    serializer_class = PatientDetailSerializer
    queryset = Patient.objects.all()


class LabResultListView(generics.ListAPIView):
    serializer_class = LabResultSerializer

    def get_queryset(self):
        return LabResult.objects.filter(
            patient_id=self.kwargs["patient_pk"]
        ).order_by("-effective_date")
