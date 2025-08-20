from django import forms
from .models import Room

class RoomCreateForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["Romname", "topic", "is_private"]
        widgets = {
            "Romname": forms.TextInput(attrs={
                "class": "border rounded w-full px-3 py-2",
                "placeholder": "방 제목",
            }),
            "topic": forms.TextInput(attrs={
                "class": "border rounded w-full px-3 py-2",
                "placeholder": "주제(예: Django)",
            }),
            "is_private": forms.CheckboxInput(attrs={
                "class": "mr-2"
            }),
        }

    def clean_Romname(self):
        v = self.cleaned_data["Romname"].strip()
        if not v:
            raise forms.ValidationError("방 제목을 입력하세요.")
        return v
