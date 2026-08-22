"""Auth-side serializers: registration (with cold-start tags) and profile."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from catalog.models import Tag
from catalog.serializers import TagSerializer

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    """POST /api/auth/register/ — username/email/password + optional quiz tags."""

    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    onboarding_tags = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Tag.objects.all(),
        many=True,
        required=False,
        default=list,
    )

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def validate_email(self, value: str) -> str:
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def create(self, validated_data: dict):
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email") or "",
            password=validated_data["password"],
        )
        # Profile is created by the post_save handler in accounts.signals; the
        # onboarding tags seed the cold-start recommender (Section 6.5.4).
        profile = user.profile
        tags = validated_data.get("onboarding_tags") or []
        if tags:
            profile.onboarding_tags.set(tags)
        return user


class UserSerializer(serializers.ModelSerializer):
    # Lives on Profile (1-1), not on User — needs an explicit source.
    onboarding_tags = TagSerializer(many=True, read_only=True, source="profile.onboarding_tags")

    class Meta:
        model = User
        fields = ("id", "username", "email", "date_joined", "onboarding_tags")


class MeSerializer(UserSerializer):
    """GET /api/auth/me/ — profile state incl. recommender readiness."""

    has_preference_vector = serializers.SerializerMethodField()
    interaction_summary = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + (
            "has_preference_vector",
            "interaction_summary",
        )

    def get_has_preference_vector(self, obj) -> bool:
        profile = getattr(obj, "profile", None)
        return bool(profile and profile.preference_vector is not None)

    def get_interaction_summary(self, obj) -> dict:
        from interactions.services import user_interaction_summary

        return user_interaction_summary(obj)


class UpdateOnboardingTagsSerializer(serializers.Serializer):
    """PATCH /api/auth/me/ body {onboarding_tags: [slug, …]}."""

    onboarding_tags = serializers.SlugRelatedField(
        slug_field="slug", queryset=Tag.objects.all(), many=True, required=True
    )


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """JWT obtain that also returns the user block for a smoother frontend."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data
