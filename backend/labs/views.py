from django.shortcuts import get_object_or_404
from rest_framework import generics

from .models import LabResult, Patient, Team
from .serializers import (
    LabResultSerializer,
    PatientDetailSerializer,
    PatientListSerializer,
)


class PatientListView(generics.ListAPIView):
    serializer_class = PatientListSerializer

    def get_queryset(self):
        return Patient.objects.filter(team__slug=self.kwargs["slug"]).order_by("name")


class PatientDetailView(generics.RetrieveAPIView):
    serializer_class = PatientDetailSerializer

    def get_queryset(self):
        return Patient.objects.filter(team__slug=self.kwargs["slug"])


class LabResultListView(generics.ListAPIView):
    serializer_class = LabResultSerializer

    def get_queryset(self):
        team = get_object_or_404(Team, slug=self.kwargs["slug"])
        patient = get_object_or_404(Patient, pk=self.kwargs["patient_pk"], team=team)
        return LabResult.objects.filter(patient=patient).order_by("-effective_date")
