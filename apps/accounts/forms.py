"""
Admin Forms — Identity Domain (Phase 1 — Step 1)

النماذج الافتراضية في django.contrib.auth.forms مربوطة بحقل username،
لذلك نعرّف نسخًا مبنية على phone.
"""

from django.contrib.auth.forms import UserChangeForm as BaseUserChangeForm
from django.contrib.auth.forms import UserCreationForm as BaseUserCreationForm

from .models import User


class UserCreationForm(BaseUserCreationForm):
    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("phone", "full_name", "email", "role", "status")


class UserChangeForm(BaseUserChangeForm):
    class Meta(BaseUserChangeForm.Meta):
        model = User
        fields = "__all__"
